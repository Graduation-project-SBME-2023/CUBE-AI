"""Live checks of the harness transactions.

Every scenario goes through the real FastAPI app (httpx + ASGI) against a
disposable PostgreSQL 18 database migrated to head, so the routes, the
repositories, the write paths and the database triggers are exercised
together.

Run (from Backend/, with a throwaway database):

    V1_LIVE_DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/db \
    python -m pytest -c pytest.ini tests/integration/test_v1_p_transactions_live.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.slow, pytest.mark.asyncio]

LIVE_DSN = os.environ.get("V1_LIVE_DATABASE_URL")
pytestmark.append(
    pytest.mark.skipif(not LIVE_DSN, reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database")
)
if LIVE_DSN:
    # The app reads its settings at import.
    os.environ["DATABASE_URL"] = LIVE_DSN
    for name in ("ORTHANC_URL", "ORTHANC_USER", "ORTHANC_PASSWORD"):
        os.environ.setdefault(name, "http://orthanc.invalid" if name == "ORTHANC_URL" else "x")
    os.environ.setdefault("INFERENCE_API_URL", "http://127.0.0.1:9")  # nothing listens: MONAI is down

httpx = pytest.importorskip("httpx")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "application_api" / "alembic.ini"


# ----------------------------------------------------------------------
# Database and client
# ----------------------------------------------------------------------
def _psql(sql: str) -> None:
    from sqlalchemy import create_engine

    sync_url = LIVE_DSN.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.exec_driver_sql(sql)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    _psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=str(BACKEND_ROOT), env=dict(os.environ, DATABASE_URL=LIVE_DSN), capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    yield


class Api:
    """A thin client: every call states the status it expects."""

    def __init__(self, client) -> None:
        self.client = client

    async def call(
        self, method: str, url: str, *, json: Any = None, expect: int = 200,
        attempt: Optional[str] = None, key: Optional[str] = None, params: Optional[dict] = None,
    ) -> Any:
        headers = {}
        if attempt:
            headers["X-Attempt-Id"] = str(attempt)
        if key:
            headers["Idempotency-Key"] = key
        response = await self.client.request(method, url, json=json, headers=headers, params=params)
        assert response.status_code == expect, f"{method} {url}: {response.status_code} {response.text}"
        return response.json() if response.content else None

    # -- building blocks ------------------------------------------------
    async def session(self, mode: str = "auto") -> str:
        body = await self.call("POST", "/sessions/", json={"prompt": "synthetic question", "execution_mode": mode},
                               expect=201)
        return body["session_id"]

    async def graph(self, session_id: str) -> dict:
        return await self.call("GET", f"/session-graphs/session/{session_id}")

    async def submit(self, session_id: str, nodes: list[dict], *, base: Optional[int] = None,
                     expect: int = 200, approval_reasons: Optional[list] = None, **content: Any) -> dict:
        if base is None:
            base = (await self.graph(session_id))["version"]
        nodes = [{k: v for k, v in item.items() if k != "session_id"} for item in nodes]
        return await self.call(
            "POST", f"/session-graphs/session/{session_id}/changes",
            json={"author": "user", "base_version": base, "approval_reasons": approval_reasons or [],
                  "content": {"v": 1, "nodes": nodes, **content}},
            key=str(uuid.uuid4()), expect=expect,
        )

    async def start(self, node: dict, *, expect: int = 201, pins: Optional[dict] = None,
                    prompt: Optional[str] = None, limits: Optional[dict] = None, **extra: Any) -> dict:
        graph = await self.graph(node["session_id"])
        live = next(item["node"] for item in graph["nodes"] if item["node"]["node_id"] == node["node_id"])
        body = {
            "node_id": node["node_id"],
            "accepted_by_change_id": live["accepted_by_change_id"],
            "dispatch_kind": "in_process",
            "resolved_inputs": {"v": 1, "ports": pins or {}},
            "limits": limits or {},
            **({"compiled_prompt": prompt} if prompt else {}),
            **extra,
        }
        return await self.call("POST", "/runs/start", json=body, expect=expect)

    async def dispatch(self, started: dict) -> None:
        await self.call("POST", self._attempt(started, "dispatched"), json={"state": "running"})

    async def complete(self, started: dict, summary: Optional[dict] = None, expect: int = 200) -> dict:
        run_id = started["run"]["run_id"]
        outputs = {"v": 1, "ports": {"summary": {
            "type": "ResultSummary", "ref": {"table": "node_runs", "ids": [run_id]}, "summary": summary or {},
        }}}
        return await self.call("POST", self._attempt(started, "complete"), json={"outputs": outputs},
                               attempt=started["attempt"]["attempt_id"], expect=expect)

    async def fail(self, started: dict) -> dict:
        return await self.call("POST", self._attempt(started, "fail"), json={"error": {"code": "error"}},
                               attempt=started["attempt"]["attempt_id"])

    @staticmethod
    def _attempt(started: dict, action: str) -> str:
        return f"/runs/{started['run']['run_id']}/attempts/{started['attempt']['attempt_id']}/{action}"


def node(ref: str = "run_modeling", kind: str = "tool", title: Optional[str] = None, **fields: Any) -> dict:
    return {"node_id": str(uuid.uuid4()), "kind": kind, "ref": ref, "ref_version": "1",
            "title": title or ref, "params": {}, **fields}


@pytest_asyncio.fixture
async def api():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from application_api.db.session import get_db
    from application_api.main import app
    from application_api.repositories import users

    users._ids.clear()
    engine = create_async_engine(LIVE_DSN, poolclass=NullPool)
    sessions = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def _db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Api(client)
    app.dependency_overrides.clear()
    await engine.dispose()


async def _events(api: Api, session_id: str) -> list[str]:
    rows = await api.call("GET", f"/session-events/session/{session_id}", params={"limit": 500})
    items = rows if isinstance(rows, list) else rows.get("events", rows.get("items", []))
    return [row.get("event_type") or row.get("type") for row in items]


async def _two_nodes(api: Api, mode: str = "auto", **second: Any) -> tuple[str, dict, dict]:
    session_id = await api.session(mode)
    first, nxt = node(title="first"), node(title="second", **second)
    await api.submit(session_id, [first, nxt])
    for item in (first, nxt):
        item["session_id"] = session_id
    return session_id, first, nxt


# ----------------------------------------------------------------------
# Session creation, input, inbox cursor
# ----------------------------------------------------------------------
async def test_t0_creates_session_graph_chat_and_opening_message(api: Api):
    session_id = await api.session()
    graph = await api.graph(session_id)
    assert (graph["version"], graph["nodes"], graph["activity"]) == (0, [], "idle")
    chats = await api.call("GET", f"/chats/session/{session_id}")
    assert len(chats) == 1
    again = await api.call("POST", f"/chats/session/{session_id}", json={}, expect=201)
    assert again["chat_id"] == chats[0]["chat_id"], "one chat per session"
    messages = await api.call("GET", f"/chat-messages/{chats[0]['chat_id']}")
    assert [(m["role"], m["text"]) for m in messages] == [("user", "synthetic question")]
    assert "user_input" in await _events(api, session_id)


async def test_t3_accepts_input_idempotently_and_t8_only_moves_forward(api: Api):
    session_id = await api.session()
    message_id = str(uuid.uuid4())
    url = f"/chats/session/{session_id}/input"
    await api.call("POST", url, json={"message_id": message_id, "text": "add patients"}, expect=201)
    await api.call("POST", url, json={"message_id": message_id, "text": "add patients"}, expect=200)
    body = await api.call("POST", url, json={"message_id": message_id, "text": "other"}, expect=409)
    assert body["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    cursor = f"/sessions/{session_id}/inbox-cursor"
    events = await api.call("GET", f"/session-events/session/{session_id}", params={"limit": 500})
    items = events if isinstance(events, list) else events.get("events", events.get("items", []))
    newest = max(item["event_id"] for item in items)
    body = await api.call("POST", cursor, json={"through_event_id": newest + 1000}, expect=422)
    assert body["detail"]["code"] == "CURSOR_AHEAD"
    state = await api.call("POST", cursor, json={"through_event_id": newest, "tokens_used": 10})
    assert (state["inbox_handled_through_event_id"], state["tokens_used"]) == (newest, 10)
    body = await api.call("POST", cursor, json={"through_event_id": newest - 1}, expect=409)
    assert body["detail"]["code"] == "CURSOR_BACKWARDS"


# ----------------------------------------------------------------------
# Graph changes
# ----------------------------------------------------------------------
async def test_auto_change_applies_at_once_and_is_idempotent(api: Api):
    session_id = await api.session()
    nodes = [node(title="a"), node(title="b")]
    key = str(uuid.uuid4())
    url = f"/session-graphs/session/{session_id}/changes"
    body = {"author": "user", "base_version": 0, "content": {"v": 1, "nodes": nodes}}
    applied = await api.call("POST", url, json=body, key=key)
    assert (applied["change"]["status"], applied["change"]["decided_by"], applied["version"]) == ("applied", "auto", 1)
    graph = await api.graph(session_id)
    assert [(n["node"]["node_seq"], n["node"]["position"]) for n in graph["nodes"]] == [(1, 1), (2, 2)]

    replay = await api.call("POST", url, json=body, key=key)
    assert replay["replayed"] and replay["change"]["change_id"] == applied["change"]["change_id"]
    other = {**body, "content": {"v": 1, "nodes": nodes[:1]}}
    assert (await api.call("POST", url, json=other, key=key, expect=409))["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert (await api.call("POST", url, json=other, key=str(uuid.uuid4()), expect=409))["detail"]["code"] == "STALE_BASE"


async def test_two_concurrent_changes_on_one_base_one_wins(api: Api):
    session_id = await api.session()
    results = await asyncio.gather(
        api.client.post(f"/session-graphs/session/{session_id}/changes", headers={"Idempotency-Key": "a"},
                        json={"author": "user", "base_version": 0, "content": {"v": 1, "nodes": [node()]}}),
        api.client.post(f"/session-graphs/session/{session_id}/changes", headers={"Idempotency-Key": "b"},
                        json={"author": "user", "base_version": 0, "content": {"v": 1, "nodes": [node()]}}),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["detail"]["code"] == "STALE_BASE"


async def test_plan_mode_proposes_and_a_user_decision_applies_by_digest(api: Api):
    session_id = await api.session("plan")
    first = await api.submit(session_id, [node(title="x")])
    assert first["change"]["status"] == "pending" and "PLAN_MODE" in first["change"]["approval_reasons"]
    second = await api.submit(session_id, [node(title="y")])
    history = await api.call("GET", f"/graph-changes/session/{session_id}")
    statuses = {c["change_id"]: c["status"] for c in history["changes"]}
    assert statuses[first["change"]["change_id"]] == "withdrawn"
    assert history["pending"]["change_id"] == second["change"]["change_id"]
    assert (await api.graph(session_id))["activity"] == "awaiting_approval"

    decision = f"/graph-changes/{second['change']['change_id']}/decision"
    wrong = await api.call("POST", decision, json={"decision": "approve", "content_digest": "0" * 64}, expect=409)
    assert wrong["detail"]["code"] == "DIGEST_MISMATCH"
    applied = await api.call(
        "POST", decision, json={"decision": "approve", "content_digest": second["change"]["content_digest"]}
    )
    assert (applied["change"]["status"], applied["change"]["decided_by"], applied["version"]) == ("applied", "user", 1)


async def test_an_auto_change_marks_the_pending_one_stale(api: Api):
    session_id = await api.session()
    await api.submit(session_id, [node()])
    pending = await api.submit(session_id, [node()], approval_reasons=["destructive"])
    assert pending["change"]["status"] == "pending"
    # Removing every node needs no approval: applied at once.
    await api.submit(session_id, [])
    change = await api.call("GET", f"/graph-changes/{pending['change']['change_id']}")
    assert change["status"] == "stale"


async def test_a_change_with_a_reused_or_invalid_node_is_refused(api: Api):
    session_id = await api.session()
    a = node()
    await api.submit(session_id, [a])
    other = await api.session()
    body = await api.submit(other, [{**a}], expect=422)
    assert body["detail"]["errors"][0]["code"] == "DUPLICATE_NODE"
    b = node(input_sources={"v": 1, "ports": {"in": {"node_id": a["node_id"], "port": "x"}}})
    body = await api.submit(session_id, [b, a], expect=422)  # the source is later in the sequence
    assert body["detail"]["errors"][0]["code"] == "BAD_INPUT_SOURCE"
    body = await api.submit(session_id, [{**a, "ref": "refit_model"}], expect=422)
    assert body["detail"]["errors"][0]["code"] == "TARGET_IMMUTABLE"


# ----------------------------------------------------------------------
# Start and completion
# ----------------------------------------------------------------------
async def test_t4_pins_the_current_completed_run_and_keeps_it_after_a_rerun(api: Api):
    session_id, first, second = await _two_nodes(api)
    await api.start(second, expect=409)  # the previous node has no completed run
    started = await api.start(first)
    assert started["first_start"] and started["attempt"]["state"] == "preparing"
    body = await api.start(second, expect=409)
    assert body["detail"]["code"] in ("ATTEMPT_ACTIVE", "NOT_READY")
    await api.dispatch(started)
    assert (await api.graph(session_id))["activity"] == "running"

    # Completion is the attempt's own write.
    await api.call("POST", Api._attempt(started, "complete"), json={"outputs": {"v": 1, "ports": {}}}, expect=403)
    await api.complete(started, {"n": 3})
    run1 = started["run"]["run_id"]

    pin = {"in": {"node_id": first["node_id"], "run_id": str(uuid.uuid4()), "port": "summary"}}
    assert (await api.start(second, pins=pin, expect=409))["detail"]["code"] == "BAD_PIN"
    pin["in"]["run_id"] = run1
    downstream = await api.start(second, pins=pin)
    assert downstream["run"]["resolved_inputs"]["ports"]["in"]["type"] == "ResultSummary"
    await api.dispatch(downstream)
    await api.complete(downstream)

    # Rerunning the first node supersedes a completed run: approval needed even in Auto.
    graph = await api.graph(session_id)
    rerun = await api.submit(session_id, [first, second], rerun_node_ids=[first["node_id"]])
    assert rerun["change"]["status"] == "pending"
    assert "supersedes_completed" in rerun["change"]["approval_reasons"]
    applied = await api.call(
        "POST", f"/graph-changes/{rerun['change']['change_id']}/decision",
        json={"decision": "approve", "content_digest": rerun["change"]["content_digest"]},
    )
    assert set(applied["superseded_run_ids"]) == {run1, downstream["run"]["run_id"]}
    assert graph["version"] + 1 == applied["version"]
    old = await api.call("GET", f"/runs/{downstream['run']['run_id']}")
    assert old["superseded_at"] and old["resolved_inputs"]["ports"]["in"]["run_id"] == run1
    fresh = await api.start(first)
    assert fresh["run"]["execution_policy"]["fresh"] is True
    history = await api.call("GET", f"/runs/node/{first['node_id']}")
    assert len(history) == 2


async def test_t6_validates_outputs_and_an_expectation_holds_the_next_node(api: Api):
    expect = {"v": 1, "rules": [{"metric": "n", "op": ">=", "value": 10}]}
    session_id = await api.session()
    first, second = node(expect=expect), node()
    await api.submit(session_id, [first, second])
    first["session_id"] = second["session_id"] = session_id
    started = await api.start(first)
    await api.dispatch(started)
    bad = {"v": 1, "ports": {"x": {"type": "Cohort", "ref": {"table": "cohorts", "ids": [str(uuid.uuid4())]}}}}
    body = await api.call("POST", Api._attempt(started, "complete"), json={"outputs": bad},
                          attempt=started["attempt"]["attempt_id"], expect=422)
    assert body["detail"]["code"] == "INVALID_OUTPUTS"
    await api.complete(started, {"n": 5})
    assert (await api.graph(session_id))["activity"] == "blocked"
    pin = {"in": {"node_id": first["node_id"], "run_id": started["run"]["run_id"], "port": "summary"}}
    assert (await api.start(second, pins=pin, expect=409))["detail"]["code"] == "NOT_READY"
    release = await api.submit(session_id, [first, second], release_hold_node_ids=[first["node_id"]])
    assert release["change"]["status"] == "applied"
    run = await api.call("GET", f"/runs/{started['run']['run_id']}")
    assert run["hold_released_by_change_id"] == release["change"]["change_id"]
    await api.start(second, pins=pin)


async def test_retries_follow_the_attempt_limit(api: Api):
    session_id, first, _ = await _two_nodes(api)
    limits = {"max_attempts": 2, "idempotent": True}
    started = await api.start(first, limits=limits)
    await api.dispatch(started)
    await api.fail(started)
    retry = await api.start(first, limits=limits)
    assert (retry["first_start"], retry["attempt"]["attempt_number"]) == (False, 2)
    await api.dispatch(retry)
    await api.fail(retry)
    assert (await api.start(first, expect=409))["detail"]["code"] == "MAX_ATTEMPTS"
    assert (await api.graph(session_id))["activity"] == "blocked"


# ----------------------------------------------------------------------
# Write-path fencing
# ----------------------------------------------------------------------
async def test_writes_are_fenced_by_attempt(api: Api):
    session_id = await api.session()
    population = node("study_population_planner_agent", "agent")
    await api.submit(session_id, [population])
    population["session_id"] = session_id
    started = await api.start(population, prompt="find the cohort")
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    url = f"/runs/{run_id}/scratchpad/register"
    # No declared path, and an attempt still preparing: both refused.
    assert (await api.call("POST", url, json={"role": "r"}, expect=409))["detail"]["code"] == "REFUSED_BY_DATABASE"
    await api.call("POST", url, json={"role": "r"}, attempt=attempt_id, expect=409)
    await api.dispatch(started)
    await api.call("POST", url, json={"role": "r"}, attempt=attempt_id)
    await api.call("PUT", f"/cohorts/run/{run_id}/main", json={}, attempt=attempt_id, expect=201)
    await api.call("PUT", f"/cohorts/run/{run_id}/other", json={}, attempt=str(uuid.uuid4()), expect=409)
    await api.fail(started)
    stale = await api.call("POST", f"/runs/{run_id}/scratchpad/notes", json={"content": "late"},
                           attempt=attempt_id, expect=409)
    assert "only a submitted or running attempt writes" in str(stale["detail"])


async def test_a_run_cannot_produce_another_targets_table(api: Api):
    session_id = await api.session()
    tool = node("run_modeling")
    await api.submit(session_id, [tool])
    tool["session_id"] = session_id
    started = await api.start(tool)
    await api.dispatch(started)
    body = await api.call("PUT", f"/cohorts/run/{started['run']['run_id']}/c", json={},
                          attempt=started["attempt"]["attempt_id"], expect=409)
    assert "rows are owned by" in str(body["detail"])


# ----------------------------------------------------------------------
# Cancellation and the privacy switch
# ----------------------------------------------------------------------
async def test_t7_cancel_is_confirmed_only_on_an_observed_stop(api: Api):
    session_id, first, _ = await _two_nodes(api)
    started = await api.start(first, limits={"max_attempts": 3})
    await api.dispatch(started)
    await api.call("POST", Api._attempt(started, "stopped"), json={}, expect=409)  # nothing was requested
    requested = await api.call("POST", Api._attempt(started, "cancel"), json={"reason": "timeout"})
    assert requested["state"] == "running" and requested["cancel_requested_at"]
    stopped = await api.call("POST", Api._attempt(started, "stopped"), json={})
    assert (stopped["state"], stopped["error"]["code"]) == ("failed", "timeout")
    retry = await api.start(first, limits={"max_attempts": 3})
    await api.dispatch(retry)
    await api.call("POST", Api._attempt(retry, "cancel"), json={"reason": "user"})
    assert (await api.call("POST", Api._attempt(retry, "stopped"), json={}))["state"] == "cancelled"
    assert (await api.start(first, expect=409))["detail"]["code"] == "CANCELLED"
    await api.start(first, explicit_retry=True)


async def test_switching_to_phi_local_cancels_standard_attempts(api: Api):
    session_id, first, _ = await _two_nodes(api)
    started = await api.start(first)
    await api.dispatch(started)
    body = await api.call("PATCH", f"/sessions/{session_id}/settings", json={"privacy_mode": "phi_local"})
    assert body["privacy_mode"] == "phi_local"
    run = await api.call("GET", f"/runs/{started['run']['run_id']}")
    assert run["attempts"][0]["cancel_requested_at"] is not None


# ----------------------------------------------------------------------
# Reconcile, suspend, answer and resume (T10); agent transcripts (D27)
# ----------------------------------------------------------------------
def _entries(*uuids: str) -> list[dict]:
    return [{"uuid": u, "type": "assistant", "message": {"content": u}} for u in uuids]


def _transcript(*uuids: str, subpath: str = "") -> dict:
    return {"subpath": subpath, "project_key": "cubeai", "entries": _entries(*uuids)}


async def _agent(api: Api) -> tuple[str, dict, dict]:
    session_id = await api.session()
    agent = node("study_population_planner_agent", "agent")
    await api.submit(session_id, [agent])
    agent["session_id"] = session_id
    started = await api.start(agent, prompt="p", limits={"resumable": True, "max_attempts": 3},
                              spec_digest="spec-1", engine_version="engine-1")
    await api.dispatch(started)
    return session_id, agent, started


async def test_t10_a_question_suspends_and_the_answer_resumes_the_same_attempt(api: Api):
    session_id, _, started = await _agent(api)
    attempt_id = started["attempt"]["attempt_id"]
    question = {"suspension": {"kind": "ask_user", "tool_use_id": "toolu_1", "payload": {"q": "wavelets?"}}}
    suspended = await api.call("POST", Api._attempt(started, "suspend"), json=question)
    assert (suspended["state"], suspended["suspension"]["v"]) == ("suspended", 2)
    assert (await api.graph(session_id))["activity"] == "awaiting_user"
    status = await api.call("GET", f"/runs/session/{session_id}/status")
    assert status["agents"][0]["waiting_for_user"] is True
    assert (await api.call("POST", Api._attempt(started, "resume"), expect=409))["detail"]["code"] == "NOT_RESUMABLE"
    reconciled = await api.call("POST", Api._attempt(started, "reconcile"), json={"observed": "not_found"})
    assert (reconciled["action"], reconciled["attempt"]["state"]) == ("unchanged", "suspended")
    wrong = await api.call("POST", Api._attempt(started, "answer"), json={"tool_use_id": "toolu_2", "answer": "y"},
                           expect=409)
    assert wrong["detail"]["code"] == "NO_OPEN_GATE"
    await api.call("POST", Api._attempt(started, "answer"), json={"tool_use_id": "toolu_1", "answer": "yes"})
    assert "gate_resolved" in await _events(api, session_id)
    await api.call("PATCH", f"/sessions/{session_id}/control-state/paused")
    await api.call("POST", Api._attempt(started, "resume"), expect=409)  # paused
    await api.call("PATCH", f"/sessions/{session_id}/control-state/active")
    resumed = await api.call("POST", Api._attempt(started, "resume"))
    assert (resumed["attempt_id"], resumed["state"]) == (attempt_id, "running")
    assert resumed["suspension"]["resume"]["answer"] == "yes"


async def test_t7_on_a_suspended_attempt_is_confirmed_at_once(api: Api):
    _, _, started = await _agent(api)
    await api.call("POST", Api._attempt(started, "suspend"), json={"suspension": {"kind": "pause"}})
    cancelled = await api.call("POST", Api._attempt(started, "cancel"), json={"reason": "user"})
    assert cancelled["state"] == "cancelled"


async def test_transcripts_are_fenced_shared_by_attempts_and_kept_while_live(api: Api):
    session_id, agent, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    url = f"/runs/{run_id}/transcript"
    assert (await api.call("POST", url, json=_transcript("m1", "m2"), attempt=attempt_id))["stored"] == 2
    replay = await api.call("POST", url, json=_transcript("m2", "m3"), attempt=attempt_id)
    assert (replay["stored"], replay["replayed"]) == (1, 1)
    await api.call("POST", url, json=_transcript("x1", subpath="subagents/agent-x"), attempt=attempt_id)
    stranger = await api.call("POST", url, json=_transcript("m9"), attempt=str(uuid.uuid4()), expect=409)
    assert stranger["detail"]["code"] == "REFUSED_BY_DATABASE"
    # Interrupted by a restart; the retry (attempt 2) reads attempt 1's session.
    reconciled = await api.call("POST", Api._attempt(started, "reconcile"), json={"observed": "not_found"})
    assert reconciled["attempt"]["state"] == "interrupted"
    await api.call("POST", url, json=_transcript("late"), attempt=attempt_id, expect=409)  # a terminal attempt
    retry = await api.start(agent, limits={"resumable": True, "max_attempts": 3})
    assert retry["attempt"]["attempt_number"] == 2
    loaded = await api.call("GET", url, attempt=retry["attempt"]["attempt_id"])
    assert [entry["uuid"] for entry in loaded["entries"]] == ["m1", "m2", "m3"]
    assert await api.call("GET", f"{url}/subpaths") == ["subagents/agent-x"]
    await api.call("GET", url, params={"subpath": "subagents/none"}, expect=404)
    purged = await api.call("POST", "/agent-transcripts/purge")
    assert purged["runs"] == 0  # a non-terminal run keeps its transcript


async def test_a_seed_applies_only_when_its_message_and_definition_match(api: Api):
    session_id, agent, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    await api.call("POST", f"/runs/{run_id}/transcript", json=_transcript("m1", "m2"), attempt=attempt_id)
    await api.complete(started)
    for message, spec, applied in (("m2", "other-spec", False), ("gone", "spec-1", False), ("m2", "spec-1", True)):
        seeded = {**agent, "seed_from": {"run_id": run_id, "message_uuid": message}}
        change = await api.submit(session_id, [seeded], rerun_node_ids=[agent["node_id"]])
        await api.call("POST", f"/graph-changes/{change['change']['change_id']}/decision",
                       json={"decision": "approve", "content_digest": change["change"]["content_digest"]})
        rerun = await api.start(agent, prompt="p", limits={"resumable": True}, spec_digest=spec,
                                engine_version="engine-1")
        assert rerun["run"]["execution_policy"]["seed_from"]["applied"] is applied
        # Nothing is copied; the seeded run's first attempt reads its seed.
        await api.call("GET", f"/runs/{rerun['run']['run_id']}/transcript", expect=404)
        await api.call("GET", f"/runs/{run_id}/transcript", attempt=rerun["attempt"]["attempt_id"])
        await api.dispatch(rerun)
        await api.complete(rerun)
    _, _, other = await _agent(api)
    forbidden = await api.call("GET", f"/runs/{run_id}/transcript", attempt=other["attempt"]["attempt_id"],
                               expect=403)
    assert forbidden["detail"]["code"] == "TRANSCRIPT_FORBIDDEN"


async def test_pending_tools_decide_the_automatic_retry(api: Api):
    session_id, agent, started = await _agent(api)
    calls = {"v": 1, "calls": [{"tool_use_id": "toolu_1", "tool": "save_cohort", "idempotent": False,
                                "started_at": "2026-10-22T10:00:00+00:00"}]}
    url = Api._attempt(started, "pending-tools")
    await api.call("PUT", url, json=calls, attempt=str(uuid.uuid4()), expect=403)  # another attempt's header
    written = await api.call("PUT", url, json=calls, attempt=started["attempt"]["attempt_id"])
    assert written["pending_tools"]["calls"][0]["tool"] == "save_cohort"
    await api.call("POST", Api._attempt(started, "reconcile"), json={"observed": "not_found"})
    refused = await api.start(agent, limits={"resumable": True, "max_attempts": 3}, expect=409)
    assert refused["detail"]["code"] == "NEEDS_DECISION"
    assert (await api.graph(session_id))["activity"] == "blocked"
    await api.start(agent, explicit_retry=True)


async def test_the_main_transcript_is_kept_per_session(api: Api):
    session_id = await api.session()
    url = f"/sessions/{session_id}/main-transcript"
    await api.call("GET", url, expect=404)
    assert (await api.call("POST", url, json=_transcript("u1", "a1")))["stored"] == 2
    assert (await api.call("POST", url, json=_transcript("a1")))["replayed"] == 1
    await api.call("POST", url, json=_transcript("c1", subpath="subagents/agent-composer"))
    assert [entry["uuid"] for entry in (await api.call("GET", url))["entries"]] == ["u1", "a1"]
    assert await api.call("GET", f"{url}/subpaths") == ["subagents/agent-composer"]


# ----------------------------------------------------------------------
# D28: the approvals the user asked for
# ----------------------------------------------------------------------
async def test_an_approval_before_node_waits_for_the_users_release(api: Api):
    session_id = await api.session()
    gated = node(title="gated", approval="before")
    await api.submit(session_id, [gated])
    gated["session_id"] = session_id
    assert (await api.graph(session_id))["activity"] == "awaiting_user"
    assert (await api.start(gated, expect=409))["detail"]["code"] == "NEEDS_RELEASE"
    by_main = await api.call(
        "POST", f"/session-graphs/session/{session_id}/changes",
        json={"author": "main", "base_version": 1, "content": {"v": 1, "nodes": [
            {k: v for k, v in gated.items() if k != "session_id"}], "release_hold_node_ids": [gated["node_id"]]},
              "tool_call": {"session_id": session_id, "tool_use_id": "toolu_release"}},
        expect=409,
    )
    assert by_main["detail"]["code"] == "USER_RELEASE_REQUIRED"
    released = await api.submit(session_id, [gated], release_hold_node_ids=[gated["node_id"]])
    graph = await api.graph(session_id)
    assert graph["nodes"][0]["node"]["start_released_by_change_id"] == released["change"]["change_id"]
    await api.start(gated)


async def test_an_approval_after_node_holds_the_next_one_for_the_users_review(api: Api):
    session_id, first, second = await _two_nodes(api)
    reviewed = {**first, "approval": "after"}
    await api.submit(session_id, [reviewed, second])
    started = await api.start(first)
    await api.dispatch(started)
    await api.complete(started)
    graph = await api.graph(session_id)
    assert (graph["nodes"][0]["run"]["downstream_hold"], graph["activity"]) == ("user_review", "awaiting_user")
    assert "approval_requested" in await _events(api, session_id)
    pin = {"in": {"node_id": first["node_id"], "run_id": started["run"]["run_id"], "port": "summary"}}
    assert (await api.start(second, pins=pin, expect=409))["detail"]["code"] == "NOT_READY"
    await api.submit(session_id, [reviewed, second], release_hold_node_ids=[first["node_id"]])
    await api.start(second, pins=pin)


async def test_only_a_change_the_user_authored_releases_at_the_database(api: Api):
    """The database itself, not only T2: a release of what the user asked to
    approve names a change the user authored, and a start waits for it."""
    session_id = await api.session()
    gated = node(title="gated", approval="before")
    proposal = await api.call(
        "POST", f"/session-graphs/session/{session_id}/changes",
        json={"author": "main", "base_version": 0, "content": {"v": 1, "nodes": [gated]},
              "tool_call": {"session_id": session_id, "tool_use_id": "toolu_add"}},
    )
    main_change = proposal["change"]["change_id"]
    release = ("UPDATE graph_nodes SET start_released_by_change_id = %s WHERE node_id = %s",
               (main_change, gated["node_id"]))
    assert "change the user authored" in _write_on_path(*release, actor="harness")


# ----------------------------------------------------------------------
# D28: automatic approval when none was asked for
# ----------------------------------------------------------------------
async def test_the_population_node_freezes_its_own_cohort_automatically(api: Api):
    _, _, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    cohort = await api.call("PUT", f"/cohorts/run/{run_id}/population", json={}, attempt=attempt_id, expect=201)
    as_user = ("UPDATE cohorts SET frozen_at = now(), frozen_by = 'user' WHERE cohort_id = %s",
               (cohort["cohort_id"],))
    assert "frozen_by is auto on this write path" in _write_on_path(*as_user, attempt_id=attempt_id)
    as_auto = ("UPDATE cohorts SET frozen_at = now(), frozen_by = 'auto' WHERE cohort_id = %s",
               (cohort["cohort_id"],))
    assert _write_on_path(*as_auto, attempt_id=attempt_id) is None


# ----------------------------------------------------------------------
# Training jobs
# ----------------------------------------------------------------------
async def test_t9_commits_the_receipt_before_an_uncertain_submission(api: Api):
    _psql(
        """
        INSERT INTO workflow (task_name, dataset_id) VALUES ('t9-task', 901);
        WITH p AS (INSERT INTO patients (orthanc_patient_id, name) VALUES ('t9-op', 't9') RETURNING patient_id),
             s AS (INSERT INTO studies (study_uid, orthanc_study_id, patient_id)
                   SELECT 't9-study', 't9-os', patient_id FROM p RETURNING study_uid),
             v AS (INSERT INTO volume_series (volume_series_instance_uid, orthanc_series_id, study_uid, num_slices)
                   SELECT 't9-vol', 't9-ov', study_uid, 1 FROM s RETURNING volume_series_instance_uid, study_uid)
        INSERT INTO workflow_details (task_name, study_uid, volume_series_instance_uid, case_index)
        SELECT 't9-task', study_uid, volume_series_instance_uid, 1 FROM v;
        """
    )
    job = await api.call("POST", "/training-jobs/", json={"task_name": "t9-task"}, expect=202)
    assert job["submitted_at"] is None and job["backend_status"] is None  # MONAI is unreachable
    body = await api.call("POST", "/training-jobs/", json={"task_name": "t9-task"}, expect=409)
    assert body["detail"]["code"] == "TRAINING_ACTIVE"
    receipts = await api.call("GET", "/training-jobs/", params={"task_name": "t9-task"})
    assert [r["job_id"] for r in receipts] == [job["job_id"]]


# ----------------------------------------------------------------------
# Ownership, the user gate path, and a write racing completion
# ----------------------------------------------------------------------
async def test_another_users_session_is_invisible(api: Api, monkeypatch):
    from application_api.core.config import settings

    mine = await api.session()
    caller = settings.DEFAULT_USERNAME
    monkeypatch.setattr(settings, "DEFAULT_USERNAME", "another-user")
    theirs, _, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    cohort = await api.call("PUT", f"/cohorts/run/{run_id}/population", json={}, attempt=attempt_id, expect=201)
    chat_id = (await api.call("GET", f"/chats/session/{theirs}"))[0]["chat_id"]
    their_event = (await api.call("GET", f"/session-events/session/{theirs}"))[0]["event_id"]
    monkeypatch.setattr(settings, "DEFAULT_USERNAME", caller)

    # 404, never 403: another user's session does not exist for this caller.
    for url in (
        f"/session-events/session/{theirs}", f"/session-events/{their_event}",
        f"/chats/{chat_id}", f"/chat-messages/{chat_id}",
        f"/cohorts/session/{theirs}", f"/cohorts/{cohort['cohort_id']}",
        f"/files/session/{theirs}", f"/reports/session/{theirs}", f"/runs/{run_id}",
    ):
        await api.call("GET", url, expect=404)
    await api.call("GET", "/session-events/filter", params={"session_id": theirs}, expect=404)
    await api.call("PUT", f"/cohorts/run/{run_id}/other", json={}, attempt=attempt_id, expect=404)
    await api.call("POST", "/session-events/", json={"session_id": theirs, "event_type": "x", "payload": {}},
                   expect=404)

    # The cross-session listing holds the caller's events and none of theirs.
    first = (await api.call("GET", f"/session-events/session/{mine}"))[0]["event_id"]
    rows = await api.call("GET", "/session-events/", params={"after_event_id": first - 1, "limit": 500})
    listed = {row["session_id"] for row in rows}
    assert mine in listed and theirs not in listed


def _write_on_path(sql: str, params: tuple, **path: str) -> Optional[str]:
    """Run one statement on a declared write path, always rolled back; the refusal or None."""
    import psycopg2

    connection = psycopg2.connect(LIVE_DSN.replace("+asyncpg", ""))
    try:
        with connection.cursor() as cursor:
            for name, value in path.items():
                cursor.execute("SELECT set_config(%s, %s, true)", (f"cubeai.{name}", value))
            cursor.execute(sql, params)
        return None
    except psycopg2.Error as exc:
        return str(exc)
    finally:
        connection.rollback()
        connection.close()


async def test_the_user_gate_path_works_only_on_a_run_suspended_on_its_form(api: Api):
    """The database itself, not only the route: the doctor's approval lands only
    while the run is running, current and suspended on that `ask_user` form."""
    _, _, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    cohort = await api.call("PUT", f"/cohorts/run/{run_id}/population", json={}, attempt=attempt_id, expect=201)
    freeze = ("UPDATE cohorts SET frozen_at = now(), frozen_by = 'user' WHERE cohort_id = %s",
              (cohort["cohort_id"],))

    assert "not suspended on its cohort_approval form" in _write_on_path(*freeze, actor="user")

    form = {"suspension": {"kind": "ask_user", "tool_use_id": "toolu_cohort", "form": "cohort_approval"}}
    await api.call("POST", Api._attempt(started, "suspend"), json=form)
    assert _write_on_path(*freeze, actor="user") is None
    rename = ("UPDATE cohorts SET cohort_key = 'renamed' WHERE cohort_id = %s", (cohort["cohort_id"],))
    assert "changes only gate columns" in _write_on_path(*rename, actor="user")
    # A domain form is answered by its own route, which writes the form's columns too.
    generic = await api.call("POST", Api._attempt(started, "answer"),
                             json={"tool_use_id": "toolu_cohort", "decision": "approve"}, expect=409)
    assert generic["detail"]["code"] == "FORM_ANSWER"

    await api.call("POST", Api._attempt(started, "cancel"), json={"reason": "user"})
    assert "not suspended on its cohort_approval form" in _write_on_path(*freeze, actor="user")


async def test_a_write_racing_completion_holds_it_off_until_it_commits(api: Api):
    """A fenced write takes FOR SHARE on its attempt, so completing the attempt waits for it."""
    import psycopg2

    session_id, _, started = await _agent(api)
    run_id, attempt_id = started["run"]["run_id"], started["attempt"]["attempt_id"]
    stored_file = (
        "INSERT INTO stored_files (session_id, created_by_run_id, kind, logical_key, "
        "display_filename, mime_type, byte_size, sha256, body, retention_class) "
        "VALUES (%s, %s, 'export_csv', %s, %s, 'text/csv', 3, %s, 'a,b'::bytea, 'retained')"
    )
    writer = psycopg2.connect(LIVE_DSN.replace("+asyncpg", ""))
    try:
        with writer.cursor() as cursor:
            cursor.execute("SELECT set_config('cubeai.attempt_id', %s, true)", (attempt_id,))
            cursor.execute(stored_file, (session_id, run_id, "raced.csv", "raced.csv", "f" * 64))
        completion = asyncio.create_task(api.complete(started))
        await asyncio.sleep(1.5)
        assert not completion.done(), "completion went ahead of an uncommitted write of its attempt"
        writer.commit()
        await asyncio.wait_for(completion, timeout=30)
    finally:
        writer.rollback()
        writer.close()

    files = await api.call("GET", f"/files/session/{session_id}")
    assert [f["logical_key"] for f in files if f["created_by_run_id"] == run_id] == ["raced.csv"]
    late = _write_on_path(stored_file, (session_id, run_id, "late.csv", "late.csv", "e" * 64),
                          attempt_id=attempt_id)
    assert "only a submitted or running attempt writes" in late


# ----------------------------------------------------------------------
# The schema the code relies on
# ----------------------------------------------------------------------
HARNESS_TABLES = {
    "users", "sessions", "session_graphs", "graph_changes", "graph_nodes", "node_runs", "run_attempts",
    "agent_transcript_entries", "main_transcript_entries", "training_jobs", "chats", "chat_messages",
}


def _sync_engine():
    from sqlalchemy import create_engine

    return create_engine(LIVE_DSN.replace("+asyncpg", "+psycopg2"))


async def test_every_session_timestamp_is_timezone_aware():
    from sqlalchemy import text

    engine = _sync_engine()
    with engine.connect() as connection:
        types = dict(connection.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'sessions' AND data_type LIKE 'timestamp%'"
        )).all())
    engine.dispose()
    assert types and set(types.values()) == {"timestamp with time zone"}, types


async def test_the_fencing_trigger_is_attached_to_every_fenced_table():
    """Fencing-trigger coverage, checked in pg_trigger (not in the migration text)."""
    import importlib.util

    # The fenced tables as of the latest revision that changed them.
    path = BACKEND_ROOT / "application_api" / "migrations" / "versions" / "t1f0a2c3d4e5_v1_t_agent_transcripts.py"
    spec = importlib.util.spec_from_file_location("_t1", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = _sync_engine()
    with engine.connect() as connection:
        attached = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE p.proname = 'domain_rows_enforce_producer' AND NOT t.tgisinternal"
            )
        }
        produced = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'created_by_run_id'"
            )
        }
    engine.dispose()
    assert attached == set(migration.FENCED_TABLES)
    assert produced <= attached


async def test_the_orm_matches_the_migrated_schema():
    """The models describe exactly the schema the migrations build: every
    table, column, type, nullability, server default, index, unique constraint
    and foreign key. Any difference is what `alembic revision --autogenerate`
    would emit, so a non-empty diff means either a model or a migration is
    missing a change. CHECK constraints, triggers and functions are not
    compared by Alembic; their own live tests cover them."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    engine = _sync_engine()
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        diffs = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert {"users", "graph_changes", "run_attempts", "training_jobs"} <= set(Base.metadata.tables)
    assert not diffs, "\n".join(map(str, diffs))
