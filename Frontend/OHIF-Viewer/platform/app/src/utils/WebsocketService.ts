import { getAgentsWsUrl } from './runtimeUrls';

class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  url = getAgentsWsUrl();
  reconnectInterval = 2000;
  private isConnecting = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

  /**
   * The highest `ws_log.event_id` seen per session, and the ids already
   * delivered.
   *
   * Every persisted event carries a globally increasing `event_id`, and
   * replay is `event_id > cursor ORDER BY event_id`. Ids are global, so gaps
   * inside one session are other sessions' events and are NOT missing —
   * the cursor must therefore be the highest id actually received, never a
   * count. The delivered set is what stops a reconnect-and-replay from
   * re-rendering an event the live socket already delivered.
   */
  private cursorBySession: Map<string, number> = new Map();
  private deliveredEventIds: Set<number> = new Set();
  private static readonly MAX_TRACKED_EVENT_IDS = 5000;

  /** The replay cursor for a session: the last event id actually received. */
  replayCursor(sessionId: string): number {
    return this.cursorBySession.get(sessionId) ?? 0;
  }

  private trackEvent(data: any): boolean {
    const eventId = typeof data?.event_id === 'number' ? data.event_id : null;
    if (eventId === null) {
      // An event the agent system could not persist is still delivered live;
      // it simply cannot be replayed, so there is nothing to dedupe against.
      return true;
    }
    if (this.deliveredEventIds.has(eventId)) {
      return false;
    }
    this.deliveredEventIds.add(eventId);
    if (this.deliveredEventIds.size > WebSocketService.MAX_TRACKED_EVENT_IDS) {
      const oldest = this.deliveredEventIds.values().next().value;
      this.deliveredEventIds.delete(oldest);
    }
    const sessionId = String(data?.session_id || '');
    if (sessionId) {
      this.cursorBySession.set(sessionId, Math.max(this.replayCursor(sessionId), eventId));
    }
    return true;
  }

  connect() {
    if (this.isConnecting || (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING))) {
      return;
    }
    this.isConnecting = true;
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => {
      this.isConnecting = false;
      this.clearReconnect();
      this.notifyStatus(true);
    };
    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (!this.trackEvent(data)) {
          return; // already delivered — a replay overlapping the live stream
        }
        this.listeners.forEach((cb) => cb(data));
      } catch (err) {
        // handle error
      }
    };
    this.socket.onclose = () => {
      this.isConnecting = false;
      this.notifyStatus(false);
      this.scheduleReconnect();
    };
    this.socket.onerror = () => {
      this.isConnecting = false;
      this.notifyStatus(false);
      this.socket?.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimeout) return;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, this.reconnectInterval);
  }

  private clearReconnect() {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  disconnect() {
    this.clearReconnect();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.isConnecting = false;
  }

  sendMessage(msg: any) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(msg));
    }
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
    return () => this.listeners.delete(callback);
  }

  onStatusChange(callback: (isConnected: boolean) => void) {
    this.statusListeners.add(callback);
    return () => this.statusListeners.delete(callback);
  }

  notifyStatus(isConnected: boolean) {
    this.statusListeners.forEach(cb => cb(isConnected));
  }

  isConnected() {
    return this.socket?.readyState === WebSocket.OPEN;
  }
}

const websocketService = new WebSocketService();
export default websocketService;