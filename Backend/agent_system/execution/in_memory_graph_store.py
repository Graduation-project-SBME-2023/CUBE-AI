"""InMemoryGraphStore: the GraphStore of the Executor's tests.

It enforces the same invariants as the database, so a
fake is never looser than the real thing: compare-and-swap on the
graph version, one pending change, the derived approval reasons, one current
run per node, one non-terminal attempt per session, the start readiness and retry
rules, attempt fencing, the `suspended` transitions, and checkpoint fencing.
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
from datetime import datetime, timezone
from typing import Any, Optional

from .graph_store import (
    Attempt,
    Change,
    ChangeResult,
    Checkpoint,
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
    return {key: node.get(key) for key in ("title", "params", "ref_version", "input_sources", "expect")}


class InMemoryGraphStore:
    def __init__(self) -> None:
        self.sessions: dict[str, _Session] = {}
        self.nodes: dict[str, _Node] = {}
        self.changes: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, _Run] = {}
        self.checkpoints: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.writes: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}

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
                    expectations=d.get("expect"), accepted_by_change_id=node.accepted_by_change_id)

    @staticmethod
    def _run_out(run: _Run) -> Run:
        return Run(run_id=run.run_id, node_id=run.node_id, status=run.status,
                   accepted_by_change_id=run.accepted_by_change_id,
                   execution_policy=copy.deepcopy(run.execution_policy), target_version=run.target_version,
                   superseded_at=_now() if run.superseded else None,
                   resolved_inputs=copy.deepcopy(run.resolved_inputs), outputs=copy.deepcopy(run.outputs),
                   downstream_hold=run.downstream_hold, hold_released_by_change_id=run.hold_released_by_change_id,
                   error=run.error, attempts=[Attempt.model_validate(a) for a in run.attempts])

    def activity(self, session_id: str) -> str:
        session = self._session(session_id)
        states = [(a["state"], (a.get("suspension") or {}).get("kind"))
                  for r in self.runs.values() if r.session_id == session_id for a in r.attempts
                  if a["state"] in ACTIVE]
        working = any(state in WORKING for state, _ in states)
        current = [self._current(n.node_id) for n in self._live(session_id)]
        hold = any(r and r.downstream_hold and not r.hold_released_by_change_id for r in current)
        stuck = any(r and r.status == "failed" and self._retry_refusal(r, False) is not None for r in current)
        if hold or stuck or any(state == "unknown" for state, _ in states):
            return "blocked"
        if session.control_state == "paused" and working:
            return "pausing"
        if any(state == "suspended" and kind == "gate" for state, kind in states):
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

    # ------------------------------------------------------------------
    # Propose / apply a change
    # ------------------------------------------------------------------
    def _plan(self, session: _Session, content: dict[str, Any]) -> dict[str, Any]:
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
        released = []
        for node_id in content.get("release_hold_node_ids", []):
            run = self._current(str(node_id))
            if run is None or not run.downstream_hold or run.hold_released_by_change_id:
                raise _refuse("INVALID_CHANGE", "BAD_RELEASE", 422)
            if str(node_id) not in affected:
                released.append(run)
        reasons = set()
        if any(r.status == "completed" for r in superseded):
            reasons.add("supersedes_completed")
        reordered = [i for i in old if i in ids] != [i for i in ids if i in by_id]
        if session.execution_mode == "plan" and (added or edited or reruns or released or reordered
                                                  or content.get("release_hold_node_ids")):
            reasons.add("PLAN_MODE")
        return {"added": added, "edited": edited, "reruns": reruns, "removed": removed, "affected": affected,
                "superseded": superseded, "released": released, "reasons": reasons, "ids": ids}

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
                    stored.accepted_by_change_id = change["change_id"]
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
            if not tool_call:
                raise _refuse("TOOL_CALL_REQUIRED", "", 422)
            idempotency_key = hashlib.sha256(f"{tool_call['turn_id']}\x1f{tool_call['call_id']}".encode()).hexdigest()
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
        plan = self._plan(session, content)
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
            plan = self._plan(session, change["content"])
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
        if latest["state"] == "interrupted" and latest.get("dispatched_at") and not explicit:
            if policy.get("resumable"):
                pending = (self._latest_checkpoint(run.run_id) or {}).get("metadata", {}).get("pending_tools", [])
                if any(not tool.get("idempotent", False) for tool in pending):
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
        earlier = [n for n in self._live(node.session_id) if n.position < node.position]
        if earlier:
            previous = self._current(earlier[-1].node_id)
            if previous is None or previous.status != "completed" or (
                    previous.downstream_hold and not previous.hold_released_by_change_id):
                raise _refuse("NOT_READY")
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
            run = _Run(str(uuid.uuid4()), node.node_id, node.session_id, node.accepted_by_change_id, policy,
                       node.definition["ref_version"], pins)
            self.runs[run.run_id] = run
            attempt = self._new_attempt(run, request)
            if entry.get("seed_from"):
                policy["seed_from"] = self._seed(node, run, attempt, entry["seed_from"], request)
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

    def _seed(self, node: _Node, run: _Run, attempt: dict, seed: dict, request: StartRequest) -> dict[str, Any]:
        frozen = {"run_id": str(seed["run_id"]), "checkpoint_id": seed["checkpoint_id"], "applied": False}
        source = self.checkpoints.get((str(seed["run_id"]), seed.get("checkpoint_ns", ""), seed["checkpoint_id"]))
        source_run = self.runs.get(str(seed["run_id"]))
        if node.kind != "agent" or not request.limits.get("resumable"):
            return {**frozen, "reason": "not a resumable agent"}
        if source is None or source_run is None:
            return {**frozen, "reason": "checkpoint not found (purged?)"}
        if source_run.node_id != node.node_id:
            return {**frozen, "reason": "checkpoint of another node"}
        if source["metadata"].get("spec_digest") != request.spec_digest:
            return {**frozen, "reason": "spec_digest differs"}
        if source_run.execution_policy.get("privacy_mode") != run.execution_policy["privacy_mode"]:
            return {**frozen, "reason": "privacy_mode differs"}
        key = (run.run_id, source["checkpoint_ns"], source["checkpoint_id"])
        self.checkpoints[key] = {**copy.deepcopy(source), "run_id": run.run_id, "attempt_id": attempt["attempt_id"],
                                 "parent_checkpoint_id": None,
                                 "metadata": {**source["metadata"], "seeded_from": {
                                     "run_id": source_run.run_id, "checkpoint_id": source["checkpoint_id"]}}}
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
                break

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

    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] != "running" or run.superseded or attempt["dispatch_kind"] != "in_process":
            raise _refuse("NOT_SUSPENDABLE")
        stored = self.checkpoints.get((run_id, "", suspension["checkpoint_id"]))
        if stored is None or stored["attempt_id"] != attempt_id:
            raise _refuse("REFUSED_BY_DATABASE", "suspend after storing the interrupt checkpoint")
        attempt.update(state="suspended", suspension={"v": 1, **suspension, "since": _now().isoformat()})
        return Attempt.model_validate(attempt)

    async def gate_decision(self, run_id: str, attempt_id: str, interrupt_id: str, decision: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        suspension = attempt.get("suspension") or {}
        if (attempt["state"] != "suspended" or suspension.get("kind") != "gate" or "resume" in suspension
                or suspension.get("interrupt_id") != interrupt_id):
            raise _refuse("NO_OPEN_GATE")
        if run.status != "running" or run.superseded:
            raise _refuse("RUN_NOT_CURRENT")
        suspension["resume"] = {"decision": decision, "decided_at": _now().isoformat()}
        return Attempt.model_validate(attempt)

    async def resume(self, run_id: str, attempt_id: str) -> Attempt:
        run, attempt = self._locate(run_id, attempt_id)
        if attempt["state"] == "running":
            return Attempt.model_validate(attempt)
        suspension = attempt.get("suspension") or {}
        session = self._session(run.session_id)
        if (attempt["state"] != "suspended" or session.control_state != "active" or run.superseded
                or (suspension.get("kind") == "gate" and "resume" not in suspension)):
            raise _refuse("NOT_RESUMABLE")
        attempt.update(state="running", resumed_at=_now())
        return Attempt.model_validate(attempt)

    # ------------------------------------------------------------------
    # Checkpoints: the latest running/suspended attempt writes
    # ------------------------------------------------------------------
    def _fence_checkpoint(self, run_id: str, attempt_id: str) -> None:
        run = self.runs.get(run_id)
        latest = run.attempts[-1] if run else None
        if latest is None or latest["attempt_id"] != attempt_id or latest["state"] not in ("running", "suspended"):
            raise _refuse("REFUSED_BY_DATABASE", "only the latest running or suspended attempt writes")

    async def put_checkpoint(self, run_id: str, attempt_id: str, *, checkpoint_id: str, checkpoint: bytes,
                             type: str, metadata: dict[str, Any], checkpoint_ns: str = "",
                             parent_checkpoint_id: Optional[str] = None) -> None:
        self._fence_checkpoint(run_id, attempt_id)
        missing = {"v", "spec_digest", "engine_version"} - metadata.keys()
        if missing:
            raise _refuse("INVALID", f"metadata needs {sorted(missing)}", 422)
        key = (run_id, checkpoint_ns, checkpoint_id)
        row = {"run_id": run_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id,
               "parent_checkpoint_id": parent_checkpoint_id, "attempt_id": attempt_id, "type": type,
               "checkpoint": checkpoint, "metadata": copy.deepcopy(metadata), "order": len(self.checkpoints)}
        existing = self.checkpoints.get(key)
        if existing is not None:
            if (existing["type"], existing["checkpoint"], existing["metadata"]) != (type, checkpoint, metadata):
                raise _refuse("CHECKPOINT_EXISTS")
            return
        self.checkpoints[key] = row

    async def put_checkpoint_writes(self, run_id: str, attempt_id: str, *, checkpoint_id: str,
                                    writes: list[dict[str, Any]], checkpoint_ns: str = "") -> int:
        self._fence_checkpoint(run_id, attempt_id)
        if (run_id, checkpoint_ns, checkpoint_id) not in self.checkpoints:
            raise _refuse("REFUSED_BY_DATABASE", "a write belongs to a stored checkpoint")
        for write in writes:
            key = (run_id, checkpoint_ns, checkpoint_id, write["task_id"], write["idx"])
            self.writes.setdefault(key, {**write, "attempt_id": attempt_id})
        return len(writes)

    def _latest_checkpoint(self, run_id: str, checkpoint_ns: str = "") -> Optional[dict[str, Any]]:
        rows = [c for (r, ns, _), c in self.checkpoints.items() if r == run_id and ns == checkpoint_ns]
        return max(rows, key=lambda c: c["order"]) if rows else None

    def _checkpoint_out(self, row: dict[str, Any]) -> Checkpoint:
        writes = [w for (r, ns, cid, _, _), w in sorted(self.writes.items())
                  if (r, ns, cid) == (row["run_id"], row["checkpoint_ns"], row["checkpoint_id"])]
        return Checkpoint.model_validate({**row, "writes": writes})

    async def get_checkpoint(self, run_id: str, checkpoint_id: Optional[str] = None,
                             checkpoint_ns: str = "") -> Optional[Checkpoint]:
        row = (self._latest_checkpoint(run_id, checkpoint_ns) if checkpoint_id is None
               else self.checkpoints.get((run_id, checkpoint_ns, checkpoint_id)))
        return self._checkpoint_out(row) if row else None

    async def list_checkpoints(self, run_id: str, *, checkpoint_ns: Optional[str] = None,
                               before: Optional[str] = None, limit: int = 20) -> list[Checkpoint]:
        rows = [c for (r, ns, cid), c in self.checkpoints.items()
                if r == run_id and (checkpoint_ns is None or ns == checkpoint_ns)
                and (before is None or cid < before)]
        rows.sort(key=lambda c: c["order"], reverse=True)
        return [self._checkpoint_out(row) for row in rows[:limit]]
