"""InMemoryGraphStore: the GraphStore of the Executor's tests.

It enforces the same invariants as the database, so a
fake is never looser than the real thing: compare-and-swap on the
graph version, one pending change, the derived approval reasons, one current
run per node, one non-terminal attempt per session, the start readiness and retry
rules, attempt fencing, the `suspended` transitions (v2: `ask_user`,
`tool_approval`, `pause`) and their answer, the node approvals the user asked
for (D28: `NEEDS_RELEASE`, `user_review`, user-only release), and transcript
fencing (D27).
The scenario table in ``tests/unit/execution/test_graph_store_scenarios.py``
runs against it, and ``tests/integration/test_v1_p_transactions_live.py``
covers the same rules against PostgreSQL.

Not modelled: the table allow-list of `outputs` refs (it needs the domain
tables) and PostgreSQL's digest (RFC 8785); the in-memory digest is a stable
SHA-256 of sorted JSON, so it is consistent within one store only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import operator
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .graph_store import (
    Attempt,
    Change,
    ChangeResult,
    GraphStoreError,
    GraphView,
    LiveNode,
    Node,
    Run,
    StartRequest,
    StartResult,
)

ACTIVE = ("preparing", "submitted", "running", "unknown", "suspended")
WORKING = ("preparing", "submitted", "running")
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
TIMEOUT_CODES = ("timeout", "gate_timeout")
# Suspensions that wait for the user's answer; a pause waits for the session to resume.
QUESTIONS = ("ask_user", "tool_approval")
FORMS = ("cohort_approval", "segmentation_review")
_OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
        ">=": operator.ge}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _refuse(code: str, message: str = "", status_code: int = 409) -> GraphStoreError:
    return GraphStoreError(code, message, status_code)


@dataclass
class _Session:
    session_id: str
    execution_mode: str
    control_state: str = "active"
    privacy_mode: str = "standard"
    version: int = 0
    next_seq: int = 0
    archived_at: Optional[datetime] = None


@dataclass
class _Node:
    node_id: str
    session_id: str
    node_seq: int
    position: int
    kind: str
    ref: str
    definition: dict[str, Any]
    accepted_by_change_id: str
    removed: bool = False
    start_released_by_change_id: Optional[str] = None


@dataclass
class _Run:
    run_id: str
    node_id: str
    session_id: str
    accepted_by_change_id: str
    execution_policy: dict[str, Any]
    target_version: str
    resolved_inputs: Optional[dict[str, Any]]
    status: str = "running"
    superseded: bool = False
    outputs: Optional[dict[str, Any]] = None
    downstream_hold: Optional[str] = None
    hold_released_by_change_id: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _definition(node: dict[str, Any]) -> dict[str, Any]:
    """The stored definition; `approval` is part of it (D28), so changing it is an edit."""
    stored = {key: node.get(key) for key in ("title", "params", "ref_version", "input_sources", "expect")}
    return {**stored, "approval": node.get("approval") or "none"}


class InMemoryGraphStore:
    def __init__(self, *, project_key: Optional[str] = None, transcript_retention_days: int = 14) -> None:
        self.sessions: dict[str, _Session] = {}
        self.nodes: dict[str, _Node] = {}
        self.changes: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, _Run] = {}
        # (run_id | session_id, subpath) -> entries in append order
        self.transcripts: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.main_transcripts: dict[tuple[str, str], list[dict[str, Any]]] = {}
        # The deployment's one key; None accepts any non-empty key.
        self.project_key = project_key
        self.transcript_retention_days = transcript_retention_days

    # ------------------------------------------------------------------
    # Sessions and reads
    # ------------------------------------------------------------------
    async def create_session(self, prompt: str, *, execution_mode: str = "auto") -> str:
        if not prompt.strip():
            raise _refuse("INVALID", "a session starts from a prompt", 422)
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = _Session(session_id, execution_mode)
        return session_id

    async def set_control_state(self, session_id: str, control_state: str) -> None:
        session = self._session(session_id)
        if session.control_state == "archived" and control_state != "archived":
            raise _refuse("ARCHIVED", "an archived session is final")
        if control_state == "archived" and session.archived_at is None:
            session.archived_at = _now()
        session.control_state = control_state

    def _session(self, session_id: str) -> _Session:
        if session_id not in self.sessions:
            raise _refuse("NOT_FOUND", f"session {session_id}", 404)
        return self.sessions[session_id]

    def _live(self, session_id: str) -> list[_Node]:
        return sorted((n for n in self.nodes.values() if n.session_id == session_id and not n.removed),
                      key=lambda n: n.position)

    def _current(self, node_id: str) -> Optional[_Run]:
        return next((r for r in self.runs.values() if r.node_id == node_id and not r.superseded), None)

    def _active_attempt(self, session_id: str) -> Optional[dict[str, Any]]:
        return next((a for r in self.runs.values() if r.session_id == session_id
                     for a in r.attempts if a["state"] in ACTIVE), None)

    def _pending(self, session_id: str) -> Optional[dict[str, Any]]:
        return next((c for c in self.changes.values()
                     if c["session_id"] == session_id and c["status"] == "pending"), None)

    def _node_out(self, node: _Node) -> Node:
        d = node.definition
        return Node(node_id=node.node_id, node_seq=node.node_seq, position=node.position, target_kind=node.kind,
                    target_name=node.ref, target_version=d["ref_version"], title=d["title"],
                    params=d.get("params") or {}, input_sources=d.get("input_sources"),
                    expectations=d.get("expect"), approval=d["approval"],
                    start_released_by_change_id=node.start_released_by_change_id,
                    accepted_by_change_id=node.accepted_by_change_id)

    @staticmethod
    def _run_out(run: _Run) -> Run:
        return Run(run_id=run.run_id, node_id=run.node_id, status=run.status,
                   accepted_by_change_id=run.accepted_by_change_id,
                   execution_policy=copy.deepcopy(run.execution_policy), target_version=run.target_version,
                   superseded_at=_now() if run.superseded else None,
                   resolved_inputs=copy.deepcopy(run.resolved_inputs), outputs=copy.deepcopy(run.outputs),
                   downstream_hold=run.downstream_hold, hold_released_by_change_id=run.hold_released_by_change_id,
                   error=run.error, attempts=[Attempt.model_validate(a) for a in run.attempts])

    @staticmethod
    def _holds(run: Optional[_Run]) -> bool:
        return bool(run and run.downstream_hold and not run.hold_released_by_change_id)

    def _previous_done(self, node: _Node, live: list[_Node]) -> bool:
        """The previous live node's current run completed with no active hold."""
        earlier = [n for n in live if n.position < node.position]
        if not earlier:
            return True
        previous = self._current(earlier[-1].node_id)
        return previous is not None and previous.status == "completed" and not self._holds(previous)

    def _awaiting_release(self, session_id: str) -> bool:
        """An `approval: before` node that is otherwise ready waits for the user."""
        session, live = self._session(session_id), self._live(session_id)
        if session.control_state != "active" or self._active_attempt(session_id) is not None:
            return False
        return any(n.definition["approval"] == "before" and not n.start_released_by_change_id
                   and self._current(n.node_id) is None and self._previous_done(n, live) for n in live)

    def activity(self, session_id: str) -> str:
        """Appendix P §P.4, in its order: blocked, pausing, awaiting_user, running,
        awaiting_approval, idle."""
        session = self._session(session_id)
        states = [(a["state"], (a.get("suspension") or {}).get("kind"))
                  for r in self.runs.values() if r.session_id == session_id for a in r.attempts
                  if a["state"] in ACTIVE]
        working = any(state in WORKING for state, _ in states)
        current = [self._current(n.node_id) for n in self._live(session_id)]
        held = [r.downstream_hold for r in current if self._holds(r)]
        stuck = any(r and r.status == "failed" and self._retry_refusal(r, False) is not None for r in current)
        if "expectation_violated" in held or stuck or any(state == "unknown" for state, _ in states):
            return "blocked"
        if session.control_state == "paused" and working:
            return "pausing"
        if (any(state == "suspended" and kind in QUESTIONS for state, kind in states)
                or "user_review" in held or self._awaiting_release(session_id)):
            return "awaiting_user"
        if working:
            return "running"
        return "awaiting_approval" if self._pending(session_id) else "idle"

    async def get_graph(self, session_id: str) -> GraphView:
        session = self._session(session_id)
        nodes = []
        for node in self._live(session_id):
            run = self._current(node.node_id)
            nodes.append(LiveNode(node=self._node_out(node), run=self._run_out(run) if run else None,
                                  attempt=Attempt.model_validate(run.attempts[-1]) if run else None))
        pending = self._pending(session_id)
        return GraphView(session_id=session_id, version=session.version, nodes=nodes,
                         pending_change=Change.model_validate(pending) if pending else None,
                         activity=self.activity(session_id))

    async def node_runs(self, node_id: str) -> list[Run]:
        if node_id not in self.nodes:
            raise _refuse("NOT_FOUND", f"node {node_id}", 404)
        return [self._run_out(run) for run in self.runs.values() if run.node_id == node_id]

    # ------------------------------------------------------------------
    # Propose / apply a change
    # ------------------------------------------------------------------
    def _plan(self, session: _Session, content: dict[str, Any], author: str) -> dict[str, Any]:
        """The apply rule: the same as plan_apply() in the API."""
        live = self._live(session.session_id)
        by_id = {n.node_id: n for n in live}
        target = content.get("nodes", [])
        ids = [str(n["node_id"]) for n in target]
        if len(ids) != len(set(ids)):
            raise _refuse("INVALID_CHANGE", "DUPLICATE_NODE", 422)
        reruns = {str(i) for i in content.get("rerun_node_ids", [])}
        added, edited = [], []
        for node in target:
            current = by_id.get(str(node["node_id"]))
            if current is None:
                if str(node["node_id"]) in self.nodes:
                    raise _refuse("INVALID_CHANGE", "DUPLICATE_NODE", 422)
                added.append(str(node["node_id"]))
            elif (node["kind"], node["ref"]) != (current.kind, current.ref):
                raise _refuse("INVALID_CHANGE", "TARGET_IMMUTABLE", 422)
            elif _definition(node) != current.definition:
                edited.append(str(node["node_id"]))
        for index, node in enumerate(target):
            for source in ((node.get("input_sources") or {}).get("ports") or {}).values():
                if str(source["node_id"]) not in ids[:index]:
                    raise _refuse("INVALID_CHANGE", "BAD_INPUT_SOURCE", 422)
        removed = [n.node_id for n in live if n.node_id not in ids]
        old = [n.node_id for n in live]
        touched = set(added) | set(edited) | reruns
        first = next((i for i in range(len(ids)) if ids[i] in touched or i >= len(old) or old[i] != ids[i]),
                     len(ids))
        affected = ids[first:] + removed
        superseded = [r for r in (self._current(i) for i in affected if i in by_id) if r is not None]
        if content.get("on_running", "reject") == "reject" and any(r.status == "running" for r in superseded):
            raise _refuse("INVALID_CHANGE", "RUNNING_NODE_AFFECTED", 422)
        # A release frees a held run, or the start of an unstarted `approval: before`
        # node. Releasing what the user asked to approve (a `user_review` hold, a
        # start) needs a change the user authored (D28).
        targets = {str(n["node_id"]): n for n in target}
        released, starts = [], []
        for node_id in map(str, content.get("release_hold_node_ids", [])):
            run = self._current(node_id)
            stored = self.nodes.get(node_id)
            accepting = node_id in added or node_id in edited or node_id in reruns
            if self._holds(run):
                if run.downstream_hold == "user_review" and author != "user":
                    raise _refuse("USER_RELEASE_REQUIRED", "only the user releases a user_review hold")
                if node_id not in affected:
                    released.append(run)
            elif (node_id in targets and (targets[node_id].get("approval") or "none") == "before"
                  and (run is None or node_id in affected)
                  and (accepting or stored is None or not stored.start_released_by_change_id)):
                if author != "user":
                    raise _refuse("USER_RELEASE_REQUIRED", "only the user releases an approval: before node")
                starts.append(node_id)
            else:
                raise _refuse("INVALID_CHANGE", "BAD_RELEASE", 422)
        reasons = set()
        if any(r.status == "completed" for r in superseded):
            reasons.add("supersedes_completed")
        reordered = [i for i in old if i in ids] != [i for i in ids if i in by_id]
        if session.execution_mode == "plan" and (added or edited or reruns or released or reordered
                                                  or content.get("release_hold_node_ids")):
            reasons.add("PLAN_MODE")
        return {"added": added, "edited": edited, "reruns": reruns, "removed": removed, "affected": affected,
                "superseded": superseded, "released": released, "starts": starts, "reasons": reasons,
                "ids": ids}

    def _apply(self, session: _Session, change: dict[str, Any], plan: dict[str, Any]) -> list[str]:
        content = change["content"]
        for position, node in enumerate(content["nodes"], start=1):
            node_id = str(node["node_id"])
            accepting = node_id in plan["added"] or node_id in plan["edited"] or node_id in plan["reruns"]
            if node_id in plan["added"]:
                session.next_seq += 1
                self.nodes[node_id] = _Node(node_id, session.session_id, session.next_seq, position, node["kind"],
                                            node["ref"], _definition(node), change["change_id"])
            else:
                stored = self.nodes[node_id]
                stored.position, stored.definition = position, _definition(node)
                if accepting:
                    # A new definition or a rerun needs a new release (T2 step 7).
                    stored.accepted_by_change_id = change["change_id"]
                    stored.start_released_by_change_id = None
        for node_id in plan["starts"]:
            self.nodes[node_id].start_released_by_change_id = change["change_id"]
        for node_id in plan["removed"]:
            self.nodes[node_id].removed = True
        superseded = []
        for run in plan["superseded"]:
            run.superseded = True
            superseded.append(run.run_id)
            attempt = next((a for a in run.attempts if a["state"] in ACTIVE), None)
            if attempt is not None:
                self._cancel(run, attempt, "harness")
        for run in plan["released"]:
            run.hold_released_by_change_id = change["change_id"]
        session.version += 1
        for other in self.changes.values():
            if other["session_id"] == session.session_id and other["status"] == "pending" \
                    and other["change_id"] != change["change_id"]:
                other["status"] = "stale"
        return superseded

    def _result(self, change, session, plan=None, superseded=(), replayed=False) -> ChangeResult:
        ids = [i for i in (plan or {}).get("ids", [])
               if i in plan["added"] or i in plan["edited"] or i in plan["reruns"]] if plan else []
        return ChangeResult(change=Change.model_validate(change), version=session.version, node_ids=ids,
                            affected=plan["affected"] if plan else [], superseded_run_ids=list(superseded),
                            replayed=replayed)

    async def submit_change(self, session_id: str, *, author: str, base_version: int, content: dict[str, Any],
                            approval_reasons: tuple[str, ...] = (), tool_call: Optional[dict] = None,
                            idempotency_key: Optional[str] = None) -> ChangeResult:
        session = self._session(session_id)
        content = {"v": 1, "rerun_node_ids": [], "release_hold_node_ids": [], "on_running": "reject",
                   **copy.deepcopy(content)}
        if author == "main":
            # D21: the Main's change is keyed by its SDK tool call, sha256(session_id || tool_use_id).
            if not tool_call or not tool_call.get("tool_use_id") or str(tool_call.get("session_id")) != session_id:
                raise _refuse("TOOL_CALL_REQUIRED", "", 422)
            idempotency_key = hashlib.sha256(f"{session_id}\x1f{tool_call['tool_use_id']}".encode()).hexdigest()
        if not idempotency_key:
            raise _refuse("IDEMPOTENCY_KEY_REQUIRED", "", 422)
        digest = _digest(content)
        existing = next((c for c in self.changes.values() if c["session_id"] == session_id
                         and c["idempotency_key"] == idempotency_key), None)
        if existing is not None:
            if existing["content_digest"] != digest:
                raise _refuse("IDEMPOTENCY_CONFLICT")
            return self._result(existing, session, replayed=True)
        if session.control_state == "archived":
            raise _refuse("ARCHIVED")
        if base_version != session.version:
            raise _refuse("STALE_BASE", f"the graph is at version {session.version}")
        plan = self._plan(session, content, author)
        reasons = sorted(set(approval_reasons) | plan["reasons"])
        change = {"change_id": str(uuid.uuid4()), "session_id": session_id, "author": author,
                  "idempotency_key": idempotency_key, "base_version": base_version, "content": content,
                  "content_digest": digest, "approval_reasons": reasons, "status": "pending",
                  "decided_by": None, "applied_version": None}
        superseded: list[str] = []
        if reasons:
            pending = self._pending(session_id)
            if pending is not None:
                pending["status"] = "withdrawn"
            self.changes[change["change_id"]] = change
        else:
            change.update(status="applied", decided_by="auto", applied_version=base_version + 1)
            self.changes[change["change_id"]] = change
            superseded = self._apply(session, change, plan)
        return self._result(change, session, plan, superseded)

    async def decide_change(self, change_id: str, decision: str, content_digest: str) -> ChangeResult:
        change = self.changes.get(change_id)
        if change is None:
            raise _refuse("NOT_FOUND", "", 404)
        session = self._session(change["session_id"])
        wanted = "applied" if decision == "approve" else "rejected"
        if change["status"] != "pending":
            if change["status"] == wanted and change["content_digest"] == content_digest:
                return self._result(change, session, replayed=True)
            raise _refuse("NOT_PENDING", change["status"])
        if content_digest != change["content_digest"]:
            raise _refuse("DIGEST_MISMATCH")
        if decision == "reject":
            change.update(status="rejected", decided_by="user")
            return self._result(change, session)
        try:
            if change["base_version"] != session.version:
                raise _refuse("STALE_BASE")
            plan = self._plan(session, change["content"], change["author"])
            if not plan["reasons"] <= set(change["approval_reasons"]):
                raise _refuse("STALE_BASE", "a derivable reason is missing")
        except GraphStoreError as exc:
            change["status"] = "stale"
            raise _refuse("STALE_BASE", exc.message) from exc
        change.update(status="applied", decided_by="user", applied_version=change["base_version"] + 1)
        superseded = self._apply(session, change, plan)
        return self._result(change, session, plan, superseded)

    # ------------------------------------------------------------------
    # Start a node
    # ------------------------------------------------------------------
    def _retry_refusal(self, run: _Run, explicit: bool) -> Optional[str]:
        latest = run.attempts[-1]
        policy = run.execution_policy
        if run.status == "cancelled" or latest["state"] == "cancelled":
            if not explicit:
                return "CANCELLED"
        elif run.status != "failed":
            return "NOT_RETRYABLE"
        if latest["state"] not in ("failed", "interrupted", "cancelled"):
            return "ATTEMPT_ACTIVE"
        if latest["attempt_number"] >= int(policy.get("max_attempts", 1)):
            return "MAX_ATTEMPTS"
        if explicit:
            return None
        # A lost transcript batch: a resume would continue from a gap (D27).
        if (latest.get("error") or {}).get("code") == "transcript_write_failed":
            return "NEEDS_DECISION"
        # Rule (f): work that may already have had effects is not repeated automatically.
        if latest["state"] == "interrupted" and latest.get("dispatched_at"):
            if policy.get("resumable"):
                pending = (latest.get("pending_tools") or {}).get("calls") or []
                if any(not call.get("idempotent", False) for call in pending):
                    return "NEEDS_DECISION"
            elif not policy.get("idempotent", False):
                return "NEEDS_DECISION"
        return None

    def _new_attempt(self, run: _Run, request: StartRequest) -> dict[str, Any]:
        number = len(run.attempts) + 1
        attempt = {"attempt_id": str(uuid.uuid4()), "run_id": run.run_id, "attempt_number": number,
                   "dispatch_kind": request.dispatch_kind, "state": "preparing", "request": request.request,
                   "submission_key": f"{run.run_id}.{number}" if request.dispatch_kind == "job" else None}
        run.attempts.append(attempt)
        return attempt

    async def start_node(self, request: StartRequest) -> StartResult:
        node = self.nodes.get(request.node_id)
        if node is None:
            raise _refuse("NOT_FOUND", "", 404)
        session = self._session(node.session_id)
        if node.removed:
            raise _refuse("NODE_REMOVED")
        if session.control_state != "active":
            raise _refuse("SESSION_NOT_ACTIVE")
        if node.accepted_by_change_id != request.accepted_by_change_id:
            raise _refuse("STALE_DEFINITION")
        live = self._live(node.session_id)
        earlier = [n for n in live if n.position < node.position]
        if not self._previous_done(node, live):
            raise _refuse("NOT_READY")
        if node.definition["approval"] == "before" and not node.start_released_by_change_id:
            raise _refuse("NEEDS_RELEASE", "the user approves this node before it starts")
        if self._active_attempt(node.session_id) is not None:
            raise _refuse("ATTEMPT_ACTIVE")
        current = self._current(node.node_id)
        if current is None:
            if (node.kind == "agent") != (request.compiled_prompt is not None):
                raise _refuse("BAD_PROMPT", "", 422)
            if request.resolved_inputs is None:
                raise _refuse("INPUTS_REQUIRED", "", 422)
            pins = self._resolve(node, earlier, request.resolved_inputs)
            change = self.changes[node.accepted_by_change_id]["content"]
            entry = next((n for n in change["nodes"] if str(n["node_id"]) == node.node_id), {})
            policy = {"v": 1, **request.limits, "privacy_mode": session.privacy_mode,
                      "fresh": node.node_id in {str(i) for i in change.get("rerun_node_ids", [])}}
            policy.update({key: getattr(request, key) for key in ("spec_digest", "engine_version")
                           if getattr(request, key) is not None})
            run = _Run(str(uuid.uuid4()), node.node_id, node.session_id, node.accepted_by_change_id, policy,
                       node.definition["ref_version"], pins)
            self.runs[run.run_id] = run
            attempt = self._new_attempt(run, request)
            if entry.get("seed_from"):
                policy["seed_from"] = self._seed(node, run, entry["seed_from"], request)
            return StartResult(run=self._run_out(run), attempt=Attempt.model_validate(attempt), first_start=True)
        refusal = self._retry_refusal(current, request.explicit_retry)
        if refusal:
            raise _refuse(refusal)
        attempt = self._new_attempt(current, request)
        current.status, current.error = "running", None
        return StartResult(run=self._run_out(current), attempt=Attempt.model_validate(attempt), first_start=False)

    def _resolve(self, node: _Node, earlier: list[_Node], inputs: dict[str, Any]) -> dict[str, Any]:
        ports = {}
        sources = ((node.definition.get("input_sources") or {}).get("ports")) or {}
        allowed = {n.node_id for n in earlier}
        for port, pin in (inputs.get("ports") or {}).items():
            upstream = self._current(str(pin["node_id"]))
            source = sources.get(port)
            produced = ((upstream.outputs or {}).get("ports") or {}).get(pin["port"]) if upstream else None
            if (str(pin["node_id"]) not in allowed or upstream is None or upstream.run_id != str(pin["run_id"])
                    or upstream.status != "completed" or produced is None
                    or (source and (str(source["node_id"]), source["port"]) != (str(pin["node_id"]), pin["port"]))):
                raise _refuse("BAD_PIN", port)
            ports[port] = {"node_id": str(pin["node_id"]), "run_id": str(pin["run_id"]), "port": pin["port"],
                           "type": produced["type"], "ref": produced["ref"]}
        return {"v": 1, "ports": ports, "target_version": node.definition["ref_version"]}

    def _seed(self, node: _Node, run: _Run, seed: dict, request: StartRequest) -> dict[str, Any]:
        """T4 step 5: check the seed and freeze the verdict. Nothing is copied:
        the engine forks the source session up to the message (Plan 9.4)."""
        frozen = {"run_id": str(seed["run_id"]), "message_uuid": seed["message_uuid"], "applied": False}
        source_run = self.runs.get(str(seed["run_id"]))
        entries = self.transcripts.get((str(seed["run_id"]), ""), [])
        if node.kind != "agent" or not request.limits.get("resumable"):
            return {**frozen, "reason": "not a resumable agent"}
        if source_run is None or not any(e["uuid"] == seed["message_uuid"] for e in entries):
            return {**frozen, "reason": "transcript or message not found (purged?)"}
        if source_run.node_id != node.node_id:
            return {**frozen, "reason": "transcript of another node"}
        for key in ("spec_digest", "engine_version"):
            if source_run.execution_policy.get(key) != getattr(request, key):
                return {**frozen, "reason": f"{key} differs"}
        if source_run.execution_policy.get("privacy_mode") != run.execution_policy["privacy_mode"]:
            return {**frozen, "reason": "privacy_mode differs"}
        return {**frozen, "applied": True}

    # ------------------------------------------------------------------
    # The attempt lifecycle
    # ------------------------------------------------------------------
    def _locate(self, run_id: str, attempt_id: str) -> tuple[_Run, dict[str, Any]]:
        run = self.runs.get(run_id)
        attempt = next((a for a in (run.attempts if run else []) if a["attempt_id"] == attempt_id), None)
        if attempt is None:
            raise _refuse("NOT_FOUND", "", 404)
        return run, attempt

    @staticmethod
    def _project(run: _Run, attempt: dict[str, Any]) -> None:
        state = attempt["state"]
        if state in ACTIVE:
            run.status, run.error = "running", None
        elif state == "succeeded":
            run.status, run.error = "completed", None
        else:
            run.status, run.error = ("cancelled" if state == "cancelled" else "failed"), attempt["error"]

    def _end(self, run: _Run, attempt: dict[str, Any], state: str, error: Optional[dict] = None) -> None:
        if state != "succeeded":
            error = error or attempt.get("error") or {"code": "cancelled" if state == "cancelled" else state}
        attempt.update(state=state, ended_at=_now(), error=error)
        self._project(run, attempt)

    def _require_latest(self, run: _Run, attempt: dict[str, Any]) -> None:
        if attempt is not run.attempts[-1]:
            raise _refuse("STALE_ATTEMPT")
        if attempt["state"] in TERMINAL:
            raise _refuse("ATTEMPT_ENDED")

    async def mark_dispatched(self, run_id: str, attempt_id: str, state: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] != "preparing":
            if attempt.get("dispatched_at") and attempt["state"] in ACTIVE:
                return Attempt.model_validate(attempt)
            raise _refuse("NOT_PREPARING")
        if attempt.get("cancel_requested_at") and state == "running":
            raise _refuse("CANCEL_REQUESTED")
        attempt.update(state=state, dispatched_at=_now())
        if state == "running":
            attempt["started_at"] = _now()
        return Attempt.model_validate(attempt)

    async def mark_running(self, run_id: str, attempt_id: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] == "running":
            return Attempt.model_validate(attempt)
        if attempt["state"] not in ("preparing", "submitted") or attempt.get("cancel_requested_at"):
            raise _refuse("NOT_STARTABLE")
        attempt.update(state="running", dispatched_at=attempt.get("dispatched_at") or _now(), started_at=_now())
        return Attempt.model_validate(attempt)

    def _succeed(self, run: _Run, attempt: dict[str, Any], outputs: dict[str, Any]) -> None:
        for port in (outputs.get("ports") or {}).values():
            if port.get("ref", {}).get("table") == "node_runs" and port["ref"]["ids"] != [run.run_id]:
                raise _refuse("INVALID_OUTPUTS", "", 422)
        run.outputs = copy.deepcopy(outputs)
        self._end(run, attempt, "succeeded")
        if run.superseded:
            return
        expectations = self.nodes[run.node_id].definition.get("expect") or {}
        for rule in expectations.get("rules", []):
            values = [p.get("summary", {}).get(rule["metric"]) for p in outputs.get("ports", {}).values()
                      if rule["metric"] in (p.get("summary") or {})]
            try:
                ok = bool(values) and _OPS[rule["op"]](values[0], rule["value"])
            except TypeError:
                ok = False
            if not ok:
                run.downstream_hold = "expectation_violated"
                return
        if self.nodes[run.node_id].definition["approval"] == "after":
            run.downstream_hold = "user_review"

    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict[str, Any]) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        self._require_latest(run, attempt)
        if attempt["state"] == "suspended":
            raise _refuse("ATTEMPT_SUSPENDED")
        self._succeed(run, attempt, outputs)
        return Attempt.model_validate(attempt)

    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict[str, Any]) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        self._require_latest(run, attempt)
        if attempt["state"] == "suspended":
            raise _refuse("ATTEMPT_SUSPENDED")
        self._end(run, attempt, "failed", error)
        return Attempt.model_validate(attempt)

    def _cancel(self, run: _Run, attempt: dict[str, Any], reason: str) -> None:
        if attempt["state"] in TERMINAL or attempt.get("cancel_requested_at"):
            return
        attempt["cancel_requested_at"] = _now()
        if reason in TIMEOUT_CODES:
            attempt["error"] = {"code": reason}
        elif reason != "user":
            attempt["error"] = {"code": "cancelled", "detail": reason}
        if attempt["state"] == "suspended":
            self._confirm(run, attempt)

    def _confirm(self, run: _Run, attempt: dict[str, Any]) -> None:
        code = (attempt.get("error") or {}).get("code")
        self._end(run, attempt, "failed" if code in TIMEOUT_CODES else "cancelled")

    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user") -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] in TERMINAL:
            raise _refuse("ATTEMPT_ENDED")
        self._cancel(run, attempt, reason)
        return Attempt.model_validate(attempt)

    async def confirm_stopped(self, run_id: str, attempt_id: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] in TERMINAL:
            return Attempt.model_validate(attempt)
        if not attempt.get("cancel_requested_at"):
            raise _refuse("NO_CANCEL_REQUEST")
        self._confirm(run, attempt)
        return Attempt.model_validate(attempt)

    async def reconcile(self, run_id: str, attempt_id: str, observed: str,
                        outputs: Optional[dict[str, Any]] = None) -> tuple[Attempt, str]:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] in TERMINAL or attempt["state"] == "suspended":
            return Attempt.model_validate(attempt), "unchanged"
        if attempt["dispatch_kind"] in ("in_process", "inline"):
            self._end(run, attempt, "interrupted", {"code": "interrupted"})
            action = "recorded"
        elif observed in ("queued", "running"):
            target = "running" if observed == "running" or attempt["state"] == "running" else "submitted"
            attempt.update(state=target, dispatched_at=attempt.get("dispatched_at") or _now())
            action = "reattach"
        elif observed == "succeeded":
            if outputs is None:
                raise _refuse("OUTPUTS_REQUIRED", "", 422)
            self._succeed(run, attempt, outputs)
            action = "recorded"
        elif observed in ("failed", "cancelled"):
            self._end(run, attempt, observed)
            action = "recorded"
        elif observed == "not_found":
            if not attempt.get("dispatched_at"):
                return Attempt.model_validate(attempt), "resubmit"
            self._end(run, attempt, "interrupted", {"code": "record_lost"})
            action = "recorded"
        else:
            attempt["state"] = "unknown"
            action = "recorded"
        return Attempt.model_validate(attempt), action

    # ------------------------------------------------------------------
    # T10: suspend, answer, resume (the same attempt throughout, I8)
    # ------------------------------------------------------------------
    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        kind, tool_use_id, form = suspension.get("kind"), suspension.get("tool_use_id"), suspension.get("form")
        if (kind not in QUESTIONS + ("pause",) or (kind == "pause") != (tool_use_id is None)
                or (form is not None and (kind != "ask_user" or form not in FORMS))):
            raise _refuse("INVALID", "a question or a tool approval names its tool_use_id; a pause does not; "
                          "only ask_user carries a form", 422)
        current = attempt.get("suspension") or {}
        if attempt["state"] == "suspended" and (current.get("kind"), current.get("tool_use_id")) == (kind, tool_use_id):
            return Attempt.model_validate(attempt)  # a replayed suspend
        if attempt["state"] != "running" or run.superseded or attempt["dispatch_kind"] != "in_process":
            raise _refuse("NOT_SUSPENDABLE")
        stored = {"v": 2, "kind": kind, "payload": copy.deepcopy(suspension.get("payload") or {}),
                  "since": _now().isoformat()}
        stored.update({k: v for k, v in (("tool_use_id", tool_use_id), ("form", form)) if v is not None})
        attempt.update(state="suspended", suspension=stored)
        return Attempt.model_validate(attempt)

    async def answer(self, run_id: str, attempt_id: str, tool_use_id: str, *, answer: Any = None,
                     decision: Optional[str] = None, comment: Optional[str] = None) -> Attempt:
        """The user's answer (user gate path): recorded on the suspension; the
        Executor, not the Main, is woken and resumes the same attempt."""
        run, attempt = self._locate(run_id, attempt_id)
        suspension = attempt.get("suspension") or {}
        if (answer is None) == (decision is None) or decision not in (None, "approve", "reject"):
            raise _refuse("INVALID", "an answer or a decision (approve | reject), not both", 422)
        if suspension.get("form") is not None:
            # A domain form is answered by its own route, which writes the form's columns too.
            raise _refuse("FORM_ANSWER", f"the attempt waits on the {suspension['form']} form")
        if (attempt["state"] != "suspended" or suspension.get("kind") not in QUESTIONS or "resume" in suspension
                or suspension.get("tool_use_id") != tool_use_id
                or (suspension["kind"] == "tool_approval" and decision is None)):
            raise _refuse("NO_OPEN_GATE")
        if run.status != "running" or run.superseded:
            raise _refuse("RUN_NOT_CURRENT")
        resume = {"answer": answer} if decision is None else {"decision": decision}
        suspension["resume"] = {**resume, "comment": comment, "decided_at": _now().isoformat()}
        return Attempt.model_validate(attempt)

    async def resume(self, run_id: str, attempt_id: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] == "running":
            return Attempt.model_validate(attempt)
        suspension = attempt.get("suspension") or {}
        session = self._session(run.session_id)
        if (attempt["state"] != "suspended" or session.control_state != "active" or run.superseded
                or (suspension.get("kind") in QUESTIONS and "resume" not in suspension)):
            raise _refuse("NOT_RESUMABLE")
        attempt.update(state="running", resumed_at=_now())
        return Attempt.model_validate(attempt)

    # ------------------------------------------------------------------
    # Agent memory: the run's latest running or suspended attempt writes
    # ------------------------------------------------------------------
    def _fence_writer(self, run_id: str, attempt_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        latest = run.attempts[-1] if run else None
        if latest is None or latest["attempt_id"] != attempt_id or latest["state"] not in ("running", "suspended"):
            raise _refuse("REFUSED_BY_DATABASE", "only the run's latest running or suspended attempt writes")
        return latest

    async def set_pending_tools(self, run_id: str, attempt_id: str, calls: list[dict[str, Any]]) -> Attempt:
        self._locate(run_id, attempt_id)  # an attempt the run does not have is NOT_FOUND
        attempt = self._fence_writer(run_id, attempt_id)
        if any(not call.get("tool_use_id") or not call.get("tool") or not isinstance(call.get("idempotent"), bool)
               for call in calls):
            raise _refuse("INVALID", "a pending call names its tool_use_id, tool and idempotent", 422)
        attempt["pending_tools"] = {"v": 1, "calls": copy.deepcopy(calls)}
        return Attempt.model_validate(attempt)

    def _check_entries(self, entries: list[dict[str, Any]], project_key: str) -> None:
        if not project_key or (self.project_key is not None and project_key != self.project_key):
            raise _refuse("PROJECT_KEY_MISMATCH", "not this deployment's project key", 422)
        if not entries or any(not isinstance(e.get("uuid"), str) or not e["uuid"] for e in entries):
            raise _refuse("INVALID", "every entry carries its uuid", 422)

    @staticmethod
    def _append(store: dict[tuple[str, str], list[dict[str, Any]]], key: tuple[str, str],
                entries: list[dict[str, Any]]) -> int:
        """A replayed entry uuid is a no-op; entries are kept unchanged and in order."""
        stored = store.setdefault(key, [])
        known = {e["uuid"] for e in stored}
        new = []
        for entry in entries:
            if entry["uuid"] not in known:
                known.add(entry["uuid"])
                new.append(copy.deepcopy(entry))
        stored.extend(new)
        return len(new)

    @staticmethod
    def _load(store: dict[tuple[str, str], list[dict[str, Any]]],
              key: tuple[str, str]) -> Optional[list[dict[str, Any]]]:
        return copy.deepcopy(store[key]) if store.get(key) else None

    @staticmethod
    def _subpaths(store: dict[tuple[str, str], list[dict[str, Any]]], owner: str) -> list[str]:
        return sorted(subpath for (key, subpath), entries in store.items() if key == owner and subpath and entries)

    def _may_read(self, run_id: str, attempt_id: Optional[str]) -> None:
        """A reading attempt belongs to the run, or is the first attempt of a
        run whose frozen seed_from names it."""
        if attempt_id is None:
            return
        for run in self.runs.values():
            attempt = next((a for a in run.attempts if a["attempt_id"] == attempt_id), None)
            if attempt is None:
                continue
            seed = run.execution_policy.get("seed_from") or {}
            if run.run_id == run_id or (attempt["attempt_number"] == 1 and seed.get("run_id") == run_id):
                return
            break
        raise _refuse("TRANSCRIPT_FORBIDDEN", "a transcript is read within its run or by its seeded run", 403)

    async def append_transcript(self, run_id: str, attempt_id: str, entries: list[dict[str, Any]], *,
                                project_key: str, subpath: str = "") -> int:
        self._fence_writer(run_id, attempt_id)
        self._check_entries(entries, project_key)
        return self._append(self.transcripts, (run_id, subpath), entries)

    async def load_transcript(self, run_id: str, *, subpath: str = "",
                              attempt_id: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
        self._may_read(run_id, attempt_id)
        return self._load(self.transcripts, (run_id, subpath))

    async def list_transcript_subpaths(self, run_id: str) -> list[str]:
        return self._subpaths(self.transcripts, run_id)

    async def append_main_transcript(self, session_id: str, entries: list[dict[str, Any]], *,
                                     project_key: str, subpath: str = "") -> int:
        self._session(session_id)
        self._check_entries(entries, project_key)
        return self._append(self.main_transcripts, (session_id, subpath), entries)

    async def load_main_transcript(self, session_id: str, *,
                                   subpath: str = "") -> Optional[list[dict[str, Any]]]:
        self._session(session_id)
        return self._load(self.main_transcripts, (session_id, subpath))

    async def list_main_transcript_subpaths(self, session_id: str) -> list[str]:
        self._session(session_id)
        return self._subpaths(self.main_transcripts, session_id)

    def _named_seeds(self) -> set[str]:
        """Runs a live seed_from still names: in a pending change, or in the
        accepting change of a live node that has not started a run from it."""
        named = set()
        for change in self.changes.values():
            for entry in change["content"].get("nodes", []):
                if not entry.get("seed_from"):
                    continue
                node = self.nodes.get(str(entry["node_id"]))
                waiting = change["status"] == "pending" or (
                    node is not None and not node.removed and node.accepted_by_change_id == change["change_id"]
                    and not any(r.node_id == node.node_id and r.accepted_by_change_id == change["change_id"]
                                for r in self.runs.values()))
                if waiting:
                    named.add(str(entry["seed_from"]["run_id"]))
        return named

    async def purge_transcripts(self) -> dict[str, int]:
        """The retention purge (harness path): runs terminal for longer than the
        retention that no live seed_from names, and the Main transcripts of
        sessions archived for longer than it."""
        cutoff = _now() - timedelta(days=self.transcript_retention_days)
        named = self._named_seeds()
        runs = {r.run_id for r in self.runs.values()
                if r.status in ("completed", "failed", "cancelled") and r.attempts
                and r.attempts[-1].get("ended_at") and r.attempts[-1]["ended_at"] <= cutoff
                and r.run_id not in named}
        sessions = {s.session_id for s in self.sessions.values() if s.archived_at and s.archived_at <= cutoff}
        run_keys = [k for k in self.transcripts if k[0] in runs]
        main_keys = [k for k in self.main_transcripts if k[0] in sessions]
        entries = sum(len(self.transcripts.pop(key)) for key in run_keys)
        for key in main_keys:
            del self.main_transcripts[key]
        return {"runs": len({k[0] for k in run_keys}), "entries": entries,
                "main_sessions": len({k[0] for k in main_keys})}
