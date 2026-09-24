import { AGENTS_WS } from '../config';

class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  errorListeners: Set<(message: string) => void> = new Set();
  // Exponential backoff with full jitter (WEBSOCKET_FIX_PLAN.md WS-12): the
  // delay doubles per consecutive failed attempt, capped, and each attempt
  // picks uniformly in [0, cap] rather than a fixed multiple of it -- so
  // many tabs reconnecting after the same outage don't all retry in
  // lockstep, forever, the way a fixed 2s interval did (fixes W20).
  private static readonly RECONNECT_BASE_MS = 1000;
  private static readonly RECONNECT_MAX_MS = 30000;
  private reconnectAttempts = 0;
  private isConnecting = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

  // The one session this tab's socket is bound to. A socket without a
  // session_id is refused by the server (1008), so there is no "connect
  // with no session" state to represent here — only "no session yet".
  private sessionId: string | null = null;
  // Marks a socket as closed on purpose (session switch, explicit
  // disconnect) so its onclose can tell that apart from a real drop: it must
  // not schedule a reconnect, and must not surface it to status listeners as
  // a disconnect (WEBSOCKET_FIX_PLAN.md WS-03/Principle 2 — switching
  // sessions means closing one socket and opening another, which is not
  // itself a connectivity event). Keyed by socket instance, not a shared
  // flag: connect() closes the old socket and opens a new one in the same
  // call, and the old socket's close event only arrives later, so a shared
  // flag could be misread by whichever socket's onclose fires next.
  private intentionalCloseSockets: WeakSet<WebSocket> = new WeakSet();

  private cursorBySession: Map<string, number> = new Map();
  // Dedup is scoped per session, not global: this singleton outlives any one
  // mount of the playground view, so a fresh mount for a session the tab has
  // already seen must be able to receive that session's events again.
  private deliveredEventIdsBySession: Map<string, Set<number>> = new Map();
  private static readonly MAX_TRACKED_EVENT_IDS = 5000;

  replayCursor(sessionId: string): number {
    return this.cursorBySession.get(sessionId) ?? 0;
  }

  getSessionId(): string | null {
    return this.sessionId;
  }

  // Call when a view is about to (re-)hydrate a session from a full replay
  // (cursor 0) so the singleton's cross-mount bookkeeping doesn't suppress
  // events this fresh mount has never actually applied to its own state.
  resetSession(sessionId: string): void {
    this.cursorBySession.delete(sessionId);
    this.deliveredEventIdsBySession.delete(sessionId);
  }

  private trackEvent(data: any): boolean {
    const eventId = typeof data?.event_id === 'number' ? data.event_id : null;
    if (eventId === null) {
      return true;
    }
    const sessionId = String(data?.session_id || '');
    let seenIds = this.deliveredEventIdsBySession.get(sessionId);
    if (!seenIds) {
      seenIds = new Set<number>();
      this.deliveredEventIdsBySession.set(sessionId, seenIds);
    }
    if (seenIds.has(eventId)) {
      return false;
    }
    seenIds.add(eventId);
    if (seenIds.size > WebSocketService.MAX_TRACKED_EVENT_IDS) {
      const oldest = seenIds.values().next().value;
      seenIds.delete(oldest);
    }
    this.cursorBySession.set(sessionId, Math.max(this.replayCursor(sessionId), eventId));
    return true;
  }

  /**
   * Open (or keep open) this tab's socket for one session. There is exactly
   * one connection per tab, bound to one session_id at a time
   * (WEBSOCKET_FIX_PLAN.md Principle 2):
   *  - no sessionId, and none set yet: nothing to open — no-op. Several
   *    consumers (chat sidebar, canvas, the notification service) call this
   *    defensively before they necessarily know the active session; there is
   *    nothing useful to do until something calls connect() with one.
   *  - sessionId differs from the one currently bound: close that socket and
   *    open a new one for the new session. A session's socket is never
   *    reused for a different session.
   *  - sessionId matches (or none was passed and one is already set): ensure
   *    the socket is open, same as the old always-on connect().
   */
  connect(sessionId?: string) {
    const nextSessionId = sessionId != null && sessionId !== '' ? String(sessionId) : this.sessionId;
    if (!nextSessionId) {
      return;
    }
    if (this.sessionId && this.sessionId !== nextSessionId) {
      this.disconnect({ intentional: true });
    }
    this.sessionId = nextSessionId;

    if (this.isConnecting || (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING))) {
      return;
    }
    this.isConnecting = true;
    // The server replays everything after this cursor as part of the
    // handshake itself (WEBSOCKET_FIX_PLAN.md WS-08) -- 0 for a session this
    // tab has never seen (or just reset via resetSession), the stored cursor
    // for a reconnect, so a dropped connection catches up automatically
    // rather than silently missing whatever happened while it was down
    // (fixes W14). There is no longer a separate HTTP replay call to race
    // against this handshake (WS-09).
    const afterEventId = this.replayCursor(this.sessionId);
    const url = `${AGENTS_WS}?session_id=${encodeURIComponent(this.sessionId)}&after_event_id=${afterEventId}`;
    console.log('[WebSocketService] Connecting to:', url);
    this.socket = new WebSocket(url);
    // Captured so onclose below always checks *this* socket's own
    // intentional-close marker, even after this.socket has moved on to a
    // later connection.
    const openedSocket = this.socket;
    openedSocket.onopen = () => {
      this.isConnecting = false;
      this.clearReconnect();
      this.reconnectAttempts = 0;
      console.log('[WebSocketService] Connected!');
      this.notifyStatus(true);
    };
    openedSocket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Defence in depth only: the server already scopes delivery to this
        // socket's own session (WS-02), so this should never fire. A frame
        // with no session_id, or a foreign one, is dropped rather than
        // trusted — this is the strict version of the filter every
        // consumer used to run for itself, inconsistently (W17).
        const frameSessionId = data?.session_id != null ? String(data.session_id) : '';
        if (!frameSessionId || frameSessionId !== this.sessionId) {
          console.warn(
            '[WebSocketService] Dropping frame for foreign/missing session:',
            frameSessionId,
            'bound session:',
            this.sessionId
          );
          return;
        }
        if (!this.trackEvent(data)) {
          return;
        }
        this.listeners.forEach((cb) => cb(data));
      } catch (err) {
        // W21: no longer swallowed -- surfaced to error listeners (e.g. the
        // notification service) in addition to the console, so a malformed
        // frame is visible in the UI rather than only in dev tools.
        console.error('[WebSocketService] Parse error:', err);
        this.notifyError('Received a malformed message from the server.');
      }
    };
    openedSocket.onclose = () => {
      console.log('[WebSocketService] Disconnected');
      this.isConnecting = false;
      const wasIntentional = this.intentionalCloseSockets.delete(openedSocket);
      if (wasIntentional) {
        return;
      }
      this.notifyStatus(false);
      this.scheduleReconnect();
    };
    openedSocket.onerror = (error) => {
      console.error('[WebSocketService] Error:', error);
      this.isConnecting = false;
      this.notifyStatus(false);
      openedSocket.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimeout) return;
    const cap = Math.min(
      WebSocketService.RECONNECT_MAX_MS,
      WebSocketService.RECONNECT_BASE_MS * 2 ** this.reconnectAttempts
    );
    const delay = Math.random() * cap;
    this.reconnectAttempts += 1;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, delay);
  }

  private clearReconnect() {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  /**
   * Close the current socket. `intentional: true` (a deliberate session
   * switch, or a caller that wants the connection gone) suppresses the
   * disconnected notification and the auto-reconnect; a plain disconnect()
   * (e.g. on unmount) behaves the same way — nothing should reconnect a
   * socket a caller asked to close.
   */
  disconnect({ intentional = true }: { intentional?: boolean } = {}) {
    this.clearReconnect();
    if (this.socket) {
      if (intentional) {
        this.intentionalCloseSockets.add(this.socket);
      }
      this.socket.close();
      this.socket = null;
    }
    this.isConnecting = false;
  }

  /**
   * Dispatch a message locally to all subscribers without sending it to the server.
   * Useful for cross-component communication (e.g., notification panel → chat sidebar).
   */
  dispatchLocal(data: any) {
    this.listeners.forEach((cb) => cb(data));
  }

  subscribe(callback: (data: any) => void) {
    this.listeners.add(callback);
    return () => {
      this.listeners.delete(callback);
    }
  }

  onStatusChange(callback: (isConnected: boolean) => void) {
    this.statusListeners.add(callback);
    return () => this.statusListeners.delete(callback);
  }

  notifyStatus(isConnected: boolean) {
    this.statusListeners.forEach(cb => cb(isConnected));
  }

  // WS-12/W21: lets a subscriber (e.g. ResearchNotificationService) surface
  // a malformed inbound frame to the user instead of it only ever reaching
  // the console.
  onError(callback: (message: string) => void) {
    this.errorListeners.add(callback);
    return () => this.errorListeners.delete(callback);
  }

  private notifyError(message: string) {
    this.errorListeners.forEach(cb => cb(message));
  }

  isConnected() {
    return this.socket?.readyState === WebSocket.OPEN;
  }
}

const websocketService = new WebSocketService();
export default websocketService;
