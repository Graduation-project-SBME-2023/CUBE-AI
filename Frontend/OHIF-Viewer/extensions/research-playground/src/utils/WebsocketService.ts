import { AGENTS_WS } from '../config';

class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  url = AGENTS_WS;
  reconnectInterval = 2000;
  private isConnecting = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private replayBuffer: any[] = [];
  private isReplaying = false;
  private replayBufferLimit = 500; // safety cap — avoid unbounded memory

  private cursorBySession: Map<string, number> = new Map();
  // Dedup is scoped per session, not global: this singleton outlives any one
  // mount of the playground view, so a fresh mount for a session the tab has
  // already seen must be able to receive that session's events again.
  private deliveredEventIdsBySession: Map<string, Set<number>> = new Map();
  private deliveredEventIdsGlobal: Set<number> = new Set();
  private static readonly MAX_TRACKED_EVENT_IDS = 5000;

  replayCursor(sessionId: string): number {
    return this.cursorBySession.get(sessionId) ?? 0;
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
    let seenIds = sessionId ? this.deliveredEventIdsBySession.get(sessionId) : this.deliveredEventIdsGlobal;
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
    console.log('[WebSocketService] Connecting to:', this.url);
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => {
      this.isConnecting = false;
      this.clearReconnect();
      console.log('[WebSocketService] Connected!');
      this.notifyStatus(true);
    };
    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (!this.trackEvent(data)) {
          return;
        }
        if (this.isReplaying && this.replayBuffer.length < this.replayBufferLimit) {
          this.replayBuffer.push(data);
        }
        this.listeners.forEach((cb) => cb(data));
      } catch (err) {
        console.error('[WebSocketService] Parse error:', err);
      }
    };
    this.socket.onclose = () => {
      console.log('[WebSocketService] Disconnected');
      this.isConnecting = false;
      this.notifyStatus(false);
      this.scheduleReconnect();
    };
    this.socket.onerror = (error) => {
      console.error('[WebSocketService] Error:', error);
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
    if (this.isReplaying && this.replayBuffer.length < this.replayBufferLimit) {
      this.replayBuffer.push(data);
    }
    this.listeners.forEach((cb) => cb(data));
  }

  subscribe(callback: (data: any) => void) {
    this.listeners.add(callback);
    // If a replay is in progress, flush buffered events to this late subscriber
    if (this.replayBuffer.length > 0) {
      this.replayBuffer.forEach((event) => callback(event));
    }
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

  isConnected() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  startReplayCapture() {
    this.isReplaying = true;
    this.replayBuffer = [];
  }

  stopReplayCapture() {
    this.isReplaying = false;
    this.replayBuffer = [];
  }
}

const websocketService = new WebSocketService();
export default websocketService;