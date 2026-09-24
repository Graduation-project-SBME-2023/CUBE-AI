import copy
import json
import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Set
from fastapi import WebSocket
from clients.backend_client import BackendClient
from models.logs import WS_Log
from services import event_registry
from services.timeout_policy import WS_SEND_TIMEOUT_SECONDS, WS_OUTBOUND_QUEUE_MAXSIZE

logger = logging.getLogger(__name__)

# Envelope schema version. Bump this, not the field set silently, if the
# shape changes again -- see WEBSOCKET_FIX_PLAN.md WS-13.
ENVELOPE_VERSION = 1

# Close codes used when the server, not the client, ends a connection.
WS_CLOSE_OVERFLOW = 1013  # "Try Again Later" -- the socket's own backlog overran
WS_CLOSE_SEND_FAILED = 1011  # send_text raised or timed out -- treat as dead
WS_CLOSE_REPLAY_FAILED = 1011  # the durable log could not be read at handshake time

# Page size for the handshake-cursor replay (WEBSOCKET_FIX_PLAN.md WS-08).
# Matches the application_api route's own default page size
# (application_api/api/routes/Wslog.py); looping this many at a time until an
# empty page, rather than one bounded call, is what fixes W3 (a session past
# 2000 events used to lose its newest ones on rehydration).
REPLAY_PAGE_SIZE = 500
# Generous: this bounds how long replay waits for one connection's own
# backlog to drain, not a network round trip -- only a connection whose
# sender task has already died (evicted, nothing draining its queue) should
# ever hit it.
REPLAY_PUT_TIMEOUT_SECONDS = 30.0

# How many recent Idempotency-Key values POST /session/{id}/input remembers
# per session (WEBSOCKET_FIX_PLAN.md WS-10). Bounded like the frontend's own
# per-session event-id dedup set (WebsocketService.ts MAX_TRACKED_EVENT_IDS)
# -- a retry only needs to be recognized within its own retry window, not
# forever.
INPUT_IDEMPOTENCY_KEYS_PER_SESSION = 200


class Connection:
    """One socket, bound to one session, with its own outbound queue and
    sender task (WEBSOCKET_FIX_PLAN.md WS-04). Identity is by instance
    (default __eq__/__hash__): a session routinely has several of these, one
    per open tab, and they are never compared to each other by value.
    """

    def __init__(self, websocket: WebSocket, session_id: str, queue_maxsize: int):
        self.websocket = websocket
        self.session_id = session_id
        self.queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=queue_maxsize)
        self.sender_task: asyncio.Task | None = None
        # The highest event_id ever delivered to this connection, by either
        # path (WS-08 principle 5 step 4 -- overlap dedup). A live event
        # committed while this connection's own replay is still paging
        # through history could otherwise reach the client twice: once as a
        # live send, once again a page later because the durable log now
        # has it too. Checked (and advanced) by both `send_ws_message`'s
        # fan-out and `replay`'s loop -- whichever delivers a given event_id
        # first wins, and the other skips it. Never reset for this
        # connection's lifetime, so a *second* replay window (there is only
        # ever one per connection today, but nothing prevents another) stays
        # just as safe.
        self.last_delivered_event_id: int | None = None


class ConnectionManager:
    def __init__(self, queue_maxsize: int = WS_OUTBOUND_QUEUE_MAXSIZE):
        # A socket is bound to exactly one session for its whole lifetime
        # (WEBSOCKET_FIX_PLAN.md WS-02): this is the routing table W1 never
        # had. connect() populates both maps together; disconnect() clears
        # both from either side, since an eviction only has the WebSocket,
        # not the session_id, at the call site.
        self.session_connections: Dict[str, Set[Connection]] = {}
        self._connections: Dict[WebSocket, Connection] = {}
        self.queue_maxsize = queue_maxsize

        # Per-session queues buffer user input for each planner session --
        # unrelated to the per-connection outbound queues above: this one is
        # inbound (client -> planner), keyed by session_id, and shared by
        # every socket on that session.
        self.message_queues: Dict[str, asyncio.Queue] = {}

        # Idempotency-Key dedup for POST /session/{id}/input (WS-10), keyed by
        # session_id then by key, insertion order so the oldest can be evicted
        # once a session's own bound is hit. A key living here means "already
        # queued" -- a retry with the same key must not double-push.
        self._input_idempotency_keys: Dict[str, "OrderedDict[str, None]"] = {}

    def _get_queue(self, session_id: str) -> asyncio.Queue:
        key = str(session_id)
        if key not in self.message_queues:
            self.message_queues[key] = asyncio.Queue()
        return self.message_queues[key]

    async def connect(self, websocket: WebSocket, session_id: str) -> Connection:
        """Accept and bind a socket to one session for its whole lifetime.

        Call only after the caller has already validated session_id (must be
        a UUID) and closed with 1008 on violation -- this method always
        accepts. A socket that isn't bound to a real session doesn't belong
        in session_connections at all, so there is no "unbound" state here.

        Returns the Connection so the caller can immediately run the
        handshake-cursor replay (WS-08) against it -- registering here, before
        that replay reads anything, is what guarantees no gap: an event
        committed while the replay is paging through the log is either in the
        page it reads or already queued on this connection, and replay()'s
        own dedup-by-cursor makes "both" harmless (principle 5).
        """
        await websocket.accept()
        session_id = str(session_id)
        connection = Connection(websocket, session_id, self.queue_maxsize)
        connection.sender_task = asyncio.create_task(self._run_sender(connection))
        self.session_connections.setdefault(session_id, set()).add(connection)
        self._connections[websocket] = connection
        logger.info("WebSocket client connected. session_id=%s", session_id)
        return connection

    def disconnect(self, websocket: WebSocket) -> None:
        """Idempotent: safe to call from both the handler's finally block and
        an eviction on the same socket without double-bookkeeping."""
        connection = self._connections.pop(websocket, None)
        if connection is None:
            return
        sockets = self.session_connections.get(connection.session_id)
        if sockets is not None:
            sockets.discard(connection)
            if not sockets:
                del self.session_connections[connection.session_id]
        task = connection.sender_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        logger.info("WebSocket client disconnected. session_id=%s", connection.session_id)

    async def _run_sender(self, connection: Connection) -> None:
        """One task per connection: drains its queue and writes to the
        socket. Emission (`send_ws_message`) never awaits this -- it only
        ever does `put_nowait` -- so one connection stuck here cannot delay
        delivery to any other connection or any other session (fixes W9)."""
        try:
            while True:
                frame = await connection.queue.get()
                try:
                    await asyncio.wait_for(
                        connection.websocket.send_text(frame),
                        timeout=WS_SEND_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "WebSocket send failed for session=%s; evicting connection",
                        connection.session_id,
                        exc_info=True,
                    )
                    await self._evict(connection, code=WS_CLOSE_SEND_FAILED)
                    return
        except asyncio.CancelledError:
            pass

    async def _evict(self, connection: Connection, *, code: int) -> None:
        self.disconnect(connection.websocket)
        try:
            await connection.websocket.close(code=code)
        except Exception:
            pass

    def clear_message_queue(self, session_id: str) -> int:
        """Discard input only when the owning session lifecycle is reset."""
        key = str(session_id)
        queue = self.message_queues.pop(key, None)
        discarded = queue.qsize() if queue is not None else 0
        if discarded:
            logger.info(
                "Discarded %s queued messages for reset session=%s",
                discarded,
                key,
            )
        self._input_idempotency_keys.pop(key, None)
        return discarded

    async def push_to_queue(
        self, raw_text: str, session_id: str, *, idempotency_key: str | None = None
    ) -> bool:
        """
        Called by POST /session/{id}/input (WEBSOCKET_FIX_PLAN.md WS-10) when
        the client sends user text. Puts it in the queue for the Planner to
        pick up.

        Returns False without queuing anything when `idempotency_key` has
        already been seen for this session -- a client retry (e.g. after a
        timed-out response whose request actually landed) must be safe to
        resend, not a second message reaching the planner. Returns True
        otherwise, including when no key was given at all (no dedup
        possible, same as before this ticket).
        """
        try:
            key = str(session_id)
            if idempotency_key:
                seen = self._input_idempotency_keys.setdefault(key, OrderedDict())
                if idempotency_key in seen:
                    logger.info(
                        "Duplicate Idempotency-Key for session=%s; not re-queuing",
                        key,
                    )
                    return False
                seen[idempotency_key] = None
                if len(seen) > INPUT_IDEMPOTENCY_KEYS_PER_SESSION:
                    seen.popitem(last=False)

            queue = self._get_queue(session_id)
            await queue.put(raw_text)
            logger.info(f"Message queued for session={session_id}: {raw_text[:100]}")
            return True
        except Exception as e:
            logger.error(f"Error processing incoming message: {e}")
            return False

    async def wait_for_user_message(self, session_id: str) -> str:
        """
        Called by the Planner/Agent.
        This blocks execution here until the Queue has an item.
        """
        # This will pause the Agent until the FastAPI route receives data
        queue = self._get_queue(session_id)
        message = await queue.get()
        return message

    @staticmethod
    def _build_envelope(
        msg_type: str,
        payload: Any,
        session_id: str,
        *,
        replaying: bool,
        event_id: int | None,
        run_id: str | None = None,
        step_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> str:
        return json.dumps({
            "v": ENVELOPE_VERSION,
            "event_id": event_id,
            "session_id": session_id,
            "run_id": run_id,
            "step_id": step_id,
            "agent_run_id": agent_run_id,
            "type": msg_type,
            "payload": payload,
            "replaying": replaying,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, default=str)

    def _deliver(self, connection: Connection, message_data: str) -> None:
        """Queue one frame for exactly this connection. Never awaits the
        socket itself (WS-04/principle 3): `put_nowait` either lands in that
        connection's own sender task's queue, or raises `QueueFull`, in which
        case the connection has fallen far enough behind that catching it up
        would itself be the bottleneck -- it is evicted (closed, not
        throttled) so the client reconnects and catches up from the durable
        log instead.
        """
        try:
            connection.queue.put_nowait(message_data)
        except asyncio.QueueFull:
            logger.warning(
                "Outbound queue full for session=%s; evicting connection",
                connection.session_id,
            )
            asyncio.create_task(self._evict(connection, code=WS_CLOSE_OVERFLOW))

    async def send_ws_message(
        self,
        msg_type: str,
        payload: Any,
        session_id: str = '',
        replaying: bool = False,
        event_id: int | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        agent_run_id: str | None = None,
    ):
        """Persist the event, then send it to every one of session_id's own
        sockets.

        Routing by session_id is the WS-02 fix for W1: this used to iterate
        every connection in the process regardless of which session it
        belonged to. run_id/step_id/agent_run_id are the WS-13 envelope
        fields -- nullable now, populated where a caller already knows them;
        nothing does yet, so they read null until the LangGraph migration
        gives events a run to attach to (WEBSOCKET_FIX_PLAN.md §9.3).

        This is for a *new, live* event with no session-scoped audience yet.
        A single connection's own catch-up replay (WS-08) uses
        `send_replaying_frame` instead, which never fans out to that
        session's other sockets (fixes W2).
        """
        # Snapshot before anything awaits: streaming tool output keeps
        # appending to the same dict, and an event whose payload changed
        # between being emitted and being persisted is not a record of what
        # happened. (This is the hazard the legacy background-save path had;
        # persisting first does not remove it, it moves it.)
        payload = copy.deepcopy(payload)
        # Callers routinely pass the session's UUID object rather than str;
        # WS_Log and the outgoing envelope both require a plain string.
        session_id = str(session_id) if session_id else session_id

        # Only a durable event is worth a commit in front of it -- an
        # ephemeral one (a progress tick a later tick supersedes) is never
        # replayed, so persisting it would just be database load with no
        # reader (WEBSOCKET_FIX_PLAN.md WS-05/W8). classify() never raises
        # (see event_registry.py) -- it is only skipped here for a replay or
        # a caller that already has an event_id, since those are echoing
        # something already classified (and persisted, if durable) the first
        # time it was emitted.
        if not replaying and event_id is None:
            event_class = event_registry.classify(msg_type)
            if event_class is event_registry.EventClass.DURABLE:
                try:
                    saved = await BackendClient.save_log(
                        WS_Log(msg_type=msg_type, payload=payload, session_id=session_id)
                    )
                    event_id = saved.event_id
                except Exception as exc:
                    logger.error(
                        "Event %s for session %s was not persisted and will not be "
                        "replayable: %s",
                        msg_type,
                        session_id,
                        exc,
                    )

        message_data = self._build_envelope(
            msg_type, payload, session_id,
            replaying=replaying, event_id=event_id,
            run_id=run_id, step_id=step_id, agent_run_id=agent_run_id,
        )

        # A session with no connected socket (nobody has this tab open right
        # now) is not an error -- a durable event is still persisted above
        # and will be there on the next replay/handshake; an ephemeral one is
        # simply missed, which is what "ephemeral" means.
        sockets = self.session_connections.get(session_id, ())
        for connection in list(sockets):
            # Overlap dedup (WS-08 principle 5 step 4): this connection's own
            # replay may already have delivered this same event_id from the
            # durable log a moment ago.
            if (
                event_id is not None
                and connection.last_delivered_event_id is not None
                and event_id <= connection.last_delivered_event_id
            ):
                continue
            self._deliver(connection, message_data)
            if event_id is not None:
                connection.last_delivered_event_id = event_id

    async def send_replaying_frame(
        self,
        connection: Connection,
        msg_type: str,
        payload: Any,
        *,
        event_id: int | None = None,
    ) -> bool:
        """Queue one `replaying=true` frame for exactly this connection, never
        this session's other sockets (WS-08 fixes W2: opening a replay used
        to broadcast a session's whole history to every tab of every
        session). Payload is not deep-copied here -- unlike `send_ws_message`,
        callers own data that is either freshly read from the database
        (`replay`) or freshly built for this one send (a catch-up snapshot),
        never a live object something else keeps mutating.

        Unlike live emission's `_deliver` (`put_nowait`, principle 3: never
        await a socket), this *waits* for queue space. Pacing catch-up to the
        socket's real send rate is correct here -- a full history has
        nowhere near as urgent a delivery deadline as a live event, and
        nothing else is blocked on this call the way another session would
        be blocked behind a shared emitter. Bounded by
        REPLAY_PUT_TIMEOUT_SECONDS so a connection whose sender task has
        already evicted it (queue full, nothing draining it any more) cannot
        hang this coroutine forever; returns whether the frame was actually
        queued, so `replay()` can stop rather than keep trying to feed a
        dead connection.
        """
        message_data = self._build_envelope(
            msg_type, payload, connection.session_id,
            replaying=True, event_id=event_id,
        )
        try:
            await asyncio.wait_for(
                connection.queue.put(message_data),
                timeout=REPLAY_PUT_TIMEOUT_SECONDS,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Replay delivery stalled for session=%s; stopping this connection's replay",
                connection.session_id,
            )
            return False

    async def replay(self, connection: Connection, after_event_id: int) -> int | None:
        """The handshake's catch-up phase (WEBSOCKET_FIX_PLAN.md WS-08,
        principle 5): page the durable log from `after_event_id` in pages of
        `REPLAY_PAGE_SIZE` until empty, deliver each page to this connection
        only, then a `replay_complete` frame carrying the cursor to resume
        from.

        Call only after `connect()` has already registered this connection --
        that ordering is what makes an event committed mid-replay land safely
        in the queue instead of being lost, since `event_id` is assigned in
        commit order per session (V1's R2 session-row lock) and every frame
        already queued or delivered here only advances the caller's cursor
        forward.

        Returns the last event_id replayed (or `after_event_id` unchanged if
        there was nothing new), or `None` if the durable log could not be
        read -- the caller must treat that as a failed handshake and close
        the socket (fixes W4: a failed replay used to look exactly like an
        empty, successful one).
        """
        session_id = connection.session_id
        cursor = after_event_id
        connection_gone = False
        try:
            while True:
                page = await BackendClient.replay_logs(
                    session_id, after_event_id=cursor, limit=REPLAY_PAGE_SIZE
                )
                if not page:
                    break
                for log in page:
                    if log.event_id is not None:
                        cursor = log.event_id
                    # Chat text has its own durable home (the chat_messages
                    # table, via /session/{id}/chat-messages) and is
                    # classified ephemeral going forward (WS-05) -- rows with
                    # this type only exist from before that, and replaying
                    # them here would duplicate history the chat view already
                    # owns. A real equality check, unlike the historical
                    # `msg_type in ('msg')` substring bug it replaces (W7).
                    if log.msg_type == "msg":
                        continue
                    # Overlap dedup, the other direction: a live send may
                    # already have delivered this exact event_id to this
                    # connection while this replay was awaiting the last
                    # page fetch.
                    if (
                        log.event_id is not None
                        and connection.last_delivered_event_id is not None
                        and log.event_id <= connection.last_delivered_event_id
                    ):
                        continue
                    delivered = await self.send_replaying_frame(
                        connection, log.msg_type, log.payload, event_id=log.event_id
                    )
                    if not delivered:
                        # The connection died mid-replay (send_replaying_frame
                        # already logged it). Nothing more to deliver to --
                        # this is not a log-read failure, so it is not W4's
                        # "replay failed" case; just stop.
                        connection_gone = True
                        break
                    if log.event_id is not None:
                        connection.last_delivered_event_id = log.event_id
                if connection_gone or len(page) < REPLAY_PAGE_SIZE:
                    break
        except Exception as exc:
            logger.error(
                "Replay failed for session=%s after_event_id=%s: %s",
                session_id, after_event_id, exc, exc_info=True,
            )
            await self.send_replaying_frame(
                connection, "replay_failed", {"after_event_id": after_event_id}
            )
            return None

        if not connection_gone:
            await self.send_replaying_frame(
                connection, "replay_complete", {"last_event_id": cursor}
            )
        return cursor


# Create a global singleton instance to be imported by main.py and planner.py
manager = ConnectionManager()


# --- Helper functions to maintain your existing import signature ---

async def send_ws_message(
    msg_type: str,
    payload: Any,
    session_id: str | None = None,
    event_id: int | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    agent_run_id: str | None = None,
):
    await manager.send_ws_message(
        msg_type,
        payload,
        session_id=session_id,
        event_id=event_id,
        run_id=run_id,
        step_id=step_id,
        agent_run_id=agent_run_id,
    )


async def wait_for_user_message(ws: WebSocket = None, session_id: str | None = None):
    # Note: We ignore the 'ws' argument here because the manager handles it globally.
    return await manager.wait_for_user_message(session_id=session_id)
