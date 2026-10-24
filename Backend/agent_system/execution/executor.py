"""The session executor: one loop per session that decides what runs next.

``SessionExecutor`` reads the session graph and acts on the store's next step
(``GraphView.next_step``):

- ``start``: take the node's resource pools, then run the node with the
  ``NodeRunner``;
- ``resume``: take the pools, then continue the node's suspended attempt;
- ``await_release``: tell the user, once per node definition, that the node
  waits for their release (``approval_requested``).

The store decides what is ready, so the loop never second-guesses it: one node
runs at a time per session, and nothing starts while the session is paused,
while another attempt is active or while a hold stops the graph.

The loop runs only while it has something to do. It exits when there is no
next step and no node of its own running, and ``wake(reason)`` starts it
again: a node finished (``completion``), the graph changed
(``graph_changed``), the session was paused or resumed (``pause_changed``), a
stop was confirmed (``stop_confirmed``), the user answered a question
(``gate_resolved``) or a pool slot was released (``pool_released``). The wake
flag is cleared before the graph is read, so a wake that arrives while the
loop reads the graph or starts a node makes it read the graph again; none is
lost.

A start the store refuses (the graph changed in between, for example) ends
without a run; the loop then waits for the next wake instead of retrying at
once, so a node that cannot start never spins the loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from ..toolkit.catalog import UnknownRefError
from ..toolkit.runtime.cancel import CancelToken
from .graph_store import GraphStore, GraphStoreError, GraphView, LiveNode
from .node_runner import NodeReport, NodeRunner
from .resources import Lease, Pools

logger = logging.getLogger(__name__)

type WakeReason = Literal[
    "start", "completion", "refused", "graph_changed", "pause_changed", "stop_confirmed",
    "gate_resolved", "pool_released",
]
# publish(event_type, payload, node_id): sends a session event; it must not raise.
type Publish = Callable[[str, Mapping[str, Any], Optional[str]], Awaitable[None]]

# Attempt states that still hold the session (one attempt at a time).
ACTIVE_STATES = frozenset({"preparing", "submitted", "running", "unknown", "suspended"})


async def _no_publish(event_type: str, payload: Mapping[str, Any], node_id: Optional[str]) -> None:
    return None


class SessionExecutor:
    """Runs one session's graph, one node at a time, against a ``GraphStore``."""

    def __init__(
        self,
        session_id: str,
        store: GraphStore,
        runner: NodeRunner,
        pools: Pools,
        *,
        publish: Optional[Publish] = None,
    ) -> None:
        self.session_id = session_id
        self.store = store
        self.runner = runner
        self.pools = pools
        self.publish: Publish = publish or _no_publish
        self.reports: list[NodeReport] = []  # every node run's report, in order
        self._wake = asyncio.Event()
        self._reasons: list[WakeReason] = []
        self._loop: Optional[asyncio.Task[None]] = None
        self._work: Optional[asyncio.Task[None]] = None
        self._cancel: Optional[CancelToken] = None
        self._announced: set[tuple[str, str]] = set()  # (node_id, accepted_by_change_id)
        self._waiting_for_pools: Optional[str] = None  # the node refused for its pools

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """True while the loop runs (it exits when there is nothing to do)."""
        return self._loop is not None and not self._loop.done()

    def start(self) -> None:
        """Start the loop if it is not running."""
        self.wake("start")

    def wake(self, reason: WakeReason) -> None:
        """Tell the loop something changed; start it again if it had exited."""
        self._reasons.append(reason)
        self._wake.set()
        if not self.running:
            self._loop = asyncio.create_task(self._run(), name=f"executor:{self.session_id}")

    async def pause(self) -> None:
        """Stop new starts. A node that is running runs to its end."""
        await self.store.set_control_state(self.session_id, "paused")
        self.wake("pause_changed")

    async def resume(self) -> None:
        """Allow starts again, and continue an attempt that suspended for the pause."""
        await self.store.set_control_state(self.session_id, "active")
        self.wake("pause_changed")

    async def stop(self) -> None:
        """Cancel the session's active attempt.

        The store records the cancel request first; then the running work is
        told to stop, and the node runner confirms the stop once it has. A
        suspended attempt has no work, so the store confirms its stop at once.
        """
        view = await self.store.get_graph(self.session_id)
        for item in view.nodes:
            if item.run is not None and item.attempt is not None and item.attempt.state in ACTIVE_STATES:
                try:
                    await self.store.request_cancel(item.run.run_id, item.attempt.attempt_id, "user")
                except GraphStoreError as refusal:
                    if refusal.code != "ATTEMPT_ENDED":  # it ended meanwhile: nothing to stop
                        raise
        if self._cancel is not None:
            self._cancel.cancel()
        self.wake("stop_confirmed")

    async def join(self) -> None:
        """Wait until the loop has exited (and the node it ran has ended)."""
        while self.running or (self._work is not None and not self._work.done()):
            if self._loop is not None and not self._loop.done():
                await asyncio.shield(self._loop)
            elif self._work is not None:
                await asyncio.shield(self._work)

    async def aclose(self) -> None:
        """Stop the loop and the node task without recording anything (process shutdown).

        The interrupted attempt is reconciled when the process starts again.
        """
        for task in (self._loop, self._work):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._loop, self._work):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    def _take_reasons(self) -> set[WakeReason]:
        self._wake.clear()
        reasons, self._reasons = set(self._reasons), []
        return reasons

    async def _run(self) -> None:
        try:
            await self._loop_body()
        except Exception:  # the next wake starts the loop again
            logger.exception("the executor of session %s stopped", self.session_id)

    async def _loop_body(self) -> None:
        while True:
            reasons: set[WakeReason] = set()
            while self._work is not None:  # one node at a time: wait until it ends
                await self._wake.wait()
                reasons |= self._take_reasons()
            reasons |= self._take_reasons()
            if reasons == {"refused"}:
                return  # nothing changed since the store refused: wait for news
            view = await self.store.get_graph(self.session_id)
            step = view.next_step
            if step is None or step.action == "await_release":
                if step is not None:
                    await self._announce_release(view, step.node_id)
                if self._wake.is_set():
                    continue  # something changed while the graph was read
                return
            live = _live(view, step.node_id)
            if live is None:  # the view changed under the step; read it again
                continue
            lease = await self.pools.try_acquire(self._resources(live))
            if lease is None:
                await self._refused_for_pools(live)
                await self._wake.wait()
                continue
            self._waiting_for_pools = None
            self._spawn(view, live, step.action, lease)

    def _resources(self, live: LiveNode) -> frozenset[str]:
        """The pools the node's target declares; none when it is not in the catalog
        (the node runner then refuses the start)."""
        try:
            spec = self.runner.catalog.resolve(live.node.target_name)
        except UnknownRefError:
            return frozenset()
        return frozenset(str(resource) for resource in getattr(spec, "resources", ()))

    def _spawn(self, view: GraphView, live: LiveNode, action: str, lease: Lease) -> None:
        cancel = CancelToken()
        node_id = live.node.node_id
        if action == "resume":
            work = self.runner.resume(view, node_id, cancel=cancel)
        else:
            work = self.runner.run(view, node_id, cancel=cancel)
        self._cancel = cancel
        self._work = asyncio.create_task(self._run_node(work, lease), name=f"node:{node_id}")

    async def _run_node(self, work: Awaitable[NodeReport], lease: Lease) -> None:
        reason: WakeReason = "completion"
        try:
            report = await work
            self.reports.append(report)
            if report.status == "not_started":
                logger.info("node %s did not start: %s %s", report.node_id, report.code, report.detail)
                reason = "refused"
        except Exception:
            logger.exception("a node of session %s failed in the executor", self.session_id)
        finally:
            lease.release()
            self._cancel = None
            self._work = None
            self.wake(reason)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    async def _send(self, event_type: str, payload: Mapping[str, Any], node_id: str) -> None:
        try:
            await self.publish(event_type, payload, node_id)
        except Exception:
            logger.exception("event %s of session %s was not sent", event_type, self.session_id)

    async def _announce_release(self, view: GraphView, node_id: str) -> None:
        live = _live(view, node_id)
        if live is None:
            return
        key = (node_id, live.node.accepted_by_change_id)
        if key in self._announced:
            return
        self._announced.add(key)
        await self._send("approval_requested", {"node_id": node_id, "kind": "start_release"}, node_id)

    async def _refused_for_pools(self, live: LiveNode) -> None:
        """Wait for a pool slot; tell the session once per wait that the node cannot start yet."""
        node_id = live.node.node_id
        self.pools.notify_on_release(lambda: self.wake("pool_released"))
        if self._waiting_for_pools == node_id:
            return
        self._waiting_for_pools = node_id
        busy = sorted(name for name in self._resources(live) if not self.pools.available(name))
        await self._send("node_start_refused", {"node_id": node_id, "reason": "resources", "pools": busy}, node_id)


def _live(view: GraphView, node_id: str) -> Optional[LiveNode]:
    return next((item for item in view.nodes if item.node.node_id == node_id), None)


# ----------------------------------------------------------------------
# A fake for the callers' tests
# ----------------------------------------------------------------------
@dataclass
class FakeExecutor:
    """Records the control calls a caller makes, in order, and runs nothing."""

    session_id: str = ""
    calls: list[tuple[str, Optional[str]]] = field(default_factory=list)

    def start(self) -> None:
        self.calls.append(("start", None))

    def wake(self, reason: WakeReason) -> None:
        self.calls.append(("wake", reason))

    async def pause(self) -> None:
        self.calls.append(("pause", None))

    async def resume(self) -> None:
        self.calls.append(("resume", None))

    async def stop(self) -> None:
        self.calls.append(("stop", None))
