"""HTTP client for the harness routes of the Application API.

One method per route. The Application API owns every invariant; this
client only carries requests and turns refusals into ``ApplicationApiError``
with the API's stable ``code`` (STALE_BASE, NOT_READY, ...).

Write paths: a write an attempt makes about itself (it marks itself running, completes
or fails, appends its transcript, sets its pending tools) sends ``X-Attempt-Id``
with that attempt's id; the other transitions are the harness's and declare no
attempt, and an answer to a suspended attempt is the user's.

The legacy ``BackendClient`` (``backend_client.py``) stays for the frozen
AutoGen pipeline until both are removed.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

ATTEMPT_HEADER = "X-Attempt-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"


def default_base_url() -> str:
    return os.environ.get("APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")).rstrip("/")


class ApplicationApiError(Exception):
    """A refusal or failure from the Application API."""

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def _error(response: httpx.Response) -> ApplicationApiError:
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict) and "code" in detail:
        return ApplicationApiError(response.status_code, detail["code"], detail.get("message", ""), detail)
    if response.status_code >= 500:
        # Never hand an internal error body to a model (CLAUDE.md, tool packs).
        return ApplicationApiError(response.status_code, "INTERNAL", "the Application API failed", None)
    code = {404: "NOT_FOUND", 410: "GONE", 422: "INVALID"}.get(response.status_code, "REFUSED")
    return ApplicationApiError(response.status_code, code, str(detail)[:2000], detail)


class HarnessClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def __aenter__(self) -> HarnessClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _call(
        self, method: str, path: str, *, json: Any = None, params: Optional[dict[str, Any]] = None,
        attempt_id: Optional[str] = None, idempotency_key: Optional[str] = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if attempt_id:
            headers[ATTEMPT_HEADER] = str(attempt_id)
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        response = await self._http().request(
            method, f"{self.base_url}{path}", json=json, params=params, headers=headers
        )
        if response.status_code >= 400:
            raise _error(response)
        return response.json() if response.content else None

    # -- sessions (create, settings, inbox) -------------------------------------
    async def create_session(self, prompt: str, *, title: Optional[str] = None, prompt_kind: str = "general",
                             execution_mode: str = "auto", privacy_mode: str = "standard") -> dict:
        return await self._call("POST", "/sessions/", json={
            "prompt": prompt, "title": title, "prompt_kind": prompt_kind,
            "execution_mode": execution_mode, "privacy_mode": privacy_mode,
        })

    async def get_session(self, session_id: str) -> dict:
        return await self._call("GET", f"/sessions/{session_id}")

    async def update_settings(self, session_id: str, *, execution_mode: Optional[str] = None,
                              privacy_mode: Optional[str] = None) -> dict:
        body = {k: v for k, v in (("execution_mode", execution_mode), ("privacy_mode", privacy_mode)) if v}
        return await self._call("PATCH", f"/sessions/{session_id}/settings", json=body)

    async def set_control_state(self, session_id: str, control_state: str) -> dict:
        return await self._call("PATCH", f"/sessions/{session_id}/control-state/{control_state}")

    async def session_activity(self, session_id: str) -> str:
        return (await self._call("GET", f"/sessions/{session_id}/activity"))["activity"]

    async def advance_inbox(self, session_id: str, through_event_id: int, tokens_used: int = 0) -> dict:
        return await self._call("POST", f"/sessions/{session_id}/inbox-cursor",
                                json={"through_event_id": through_event_id, "tokens_used": tokens_used})

    # -- graph ---------------------------------------------------
    async def get_graph(self, session_id: str) -> dict:
        return await self._call("GET", f"/session-graphs/session/{session_id}")

    async def submit_change(self, session_id: str, *, author: str, base_version: int, content: dict,
                            approval_reasons: Optional[list[str]] = None, tool_call: Optional[dict] = None,
                            idempotency_key: Optional[str] = None) -> dict:
        body = {"author": author, "base_version": base_version, "content": content,
                "approval_reasons": approval_reasons or []}
        if tool_call:
            body["tool_call"] = tool_call
        return await self._call("POST", f"/session-graphs/session/{session_id}/changes", json=body,
                                idempotency_key=idempotency_key)

    async def decide_change(self, change_id: str, decision: str, content_digest: str,
                            comment: Optional[str] = None) -> dict:
        return await self._call("POST", f"/graph-changes/{change_id}/decision",
                                json={"decision": decision, "content_digest": content_digest, "comment": comment})

    async def list_changes(self, session_id: str) -> dict:
        return await self._call("GET", f"/graph-changes/session/{session_id}")

    # -- chat and the Context Manager ---------------------------------
    async def accept_input(self, session_id: str, message_id: str, text: str,
                           metadata: Optional[dict] = None) -> dict:
        return await self._call("POST", f"/chats/session/{session_id}/input",
                                json={"message_id": message_id, "text": text, "metadata": metadata})

    async def session_chat(self, session_id: str) -> dict:
        return (await self._call("GET", f"/chats/session/{session_id}"))[0]

    async def add_chat_message(self, chat_id: str, role: str, text: str, *, metadata: Optional[dict] = None,
                               message_id: Optional[str] = None) -> dict:
        body = {"role": role, "text": text, "metadata": metadata}
        if message_id:
            body["message_id"] = message_id
        return await self._call("POST", f"/chat-messages/{chat_id}", json=body)

    async def list_chat_messages(self, chat_id: str, skip: int = 0, limit: int = 100) -> list[dict]:
        return await self._call("GET", f"/chat-messages/{chat_id}", params={"skip": skip, "limit": limit})

    # -- runs and attempts (dispatch, reconcile, complete, cancel, suspend)
    async def start_node(self, request: dict) -> dict:
        return await self._call("POST", "/runs/start", json=request)

    async def get_run(self, run_id: str) -> dict:
        return await self._call("GET", f"/runs/{run_id}")

    async def node_runs(self, node_id: str) -> list[dict]:
        return await self._call("GET", f"/runs/node/{node_id}")

    def _attempt(self, run_id: str, attempt_id: str, action: str) -> str:
        return f"/runs/{run_id}/attempts/{attempt_id}/{action}"

    async def mark_dispatched(self, run_id: str, attempt_id: str, state: str) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "dispatched"), json={"state": state})

    async def mark_running(self, run_id: str, attempt_id: str) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "running"), attempt_id=attempt_id)

    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict,
                               usage: Optional[dict] = None) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "complete"),
                                json={"outputs": outputs, "usage": usage}, attempt_id=attempt_id)

    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict, usage: Optional[dict] = None) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "fail"),
                                json={"error": error, "usage": usage}, attempt_id=attempt_id)

    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user",
                             detail: Optional[str] = None) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "cancel"),
                                json={"reason": reason, "detail": detail})

    async def confirm_stopped(self, run_id: str, attempt_id: str, usage: Optional[dict] = None) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "stopped"), json={"usage": usage})

    async def reconcile_attempt(self, run_id: str, attempt_id: str, observed: str, **report: Any) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "reconcile"),
                                json={"observed": observed, **report})

    async def suspend_attempt(self, run_id: str, attempt_id: str, suspension: dict) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "suspend"),
                                json={"suspension": suspension})

    async def answer_attempt(self, run_id: str, attempt_id: str, tool_use_id: str, *, answer: Any = None,
                             decision: Optional[str] = None, comment: Optional[str] = None) -> dict:
        """T10's answer step: the user's answer to an `ask_user` question, or
        decision on a tool approval (user path, no attempt header)."""
        body = {"tool_use_id": tool_use_id, "comment": comment}
        body.update({k: v for k, v in (("answer", answer), ("decision", decision)) if v is not None})
        return await self._call("POST", self._attempt(run_id, attempt_id, "answer"), json=body)

    async def resume_attempt(self, run_id: str, attempt_id: str) -> dict:
        return await self._call("POST", self._attempt(run_id, attempt_id, "resume"))

    async def set_pending_tools(self, run_id: str, attempt_id: str, calls: list[dict]) -> dict:
        return await self._call("PUT", self._attempt(run_id, attempt_id, "pending-tools"),
                                json={"v": 1, "calls": calls}, attempt_id=attempt_id)

    # -- agent transcripts (D27) and the Main's (D21) -------------------
    async def _load(self, path: str, subpath: str, attempt_id: Optional[str] = None) -> Optional[list[dict]]:
        try:
            found = await self._call("GET", path, params={"subpath": subpath}, attempt_id=attempt_id)
        except ApplicationApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        return found["entries"]

    async def append_transcript(self, run_id: str, attempt_id: str, entries: list[dict], *,
                                project_key: str, subpath: str = "") -> dict:
        return await self._call("POST", f"/runs/{run_id}/transcript", attempt_id=attempt_id,
                                json={"subpath": subpath, "project_key": project_key, "entries": entries})

    async def load_transcript(self, run_id: str, *, subpath: str = "",
                              attempt_id: Optional[str] = None) -> Optional[list[dict]]:
        return await self._load(f"/runs/{run_id}/transcript", subpath, attempt_id)

    async def list_transcript_subpaths(self, run_id: str) -> list[str]:
        return await self._call("GET", f"/runs/{run_id}/transcript/subpaths")

    async def append_main_transcript(self, session_id: str, entries: list[dict], *,
                                     project_key: str, subpath: str = "") -> dict:
        return await self._call("POST", f"/sessions/{session_id}/main-transcript",
                                json={"subpath": subpath, "project_key": project_key, "entries": entries})

    async def load_main_transcript(self, session_id: str, *, subpath: str = "") -> Optional[list[dict]]:
        return await self._load(f"/sessions/{session_id}/main-transcript", subpath)

    async def list_main_transcript_subpaths(self, session_id: str) -> list[str]:
        return await self._call("GET", f"/sessions/{session_id}/main-transcript/subpaths")

    async def purge_transcripts(self) -> dict:
        return await self._call("POST", "/agent-transcripts/purge")

    # -- training ------------------------------------------------------
    async def start_training(self, task_name: str) -> dict:
        return await self._call("POST", "/training-jobs/", json={"task_name": task_name})

    async def get_training_job(self, job_id: str) -> dict:
        return await self._call("GET", f"/training-jobs/{job_id}")

    async def cancel_training_job(self, job_id: str) -> dict:
        return await self._call("DELETE", f"/training-jobs/{job_id}")
