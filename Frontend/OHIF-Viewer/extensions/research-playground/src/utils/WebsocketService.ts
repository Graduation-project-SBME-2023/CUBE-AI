import { AGENTS_WS } from '../config';

type Listener = (data: any) => void;

export class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<Listener> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  readonly url = AGENTS_WS;

  private activeSessionId: string | null = null;
  private isConnecting = false;
  private intentionalDisconnect = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private connectionGeneration = 0;
  private pendingMessages: any[] = [];
  private readonly pendingMessageLimit = 32;
  private replayBuffer: any[] = [];
  private isReplaying = false;
  private replayGeneration = 0;
  private readonly replayBufferLimit = 500;
  private seenEventIds: Set<string> = new Set();
  private seenEventOrder: string[] = [];
  private readonly seenEventLimit = 2000;

  connect(sessionId?: string | number | null): boolean {
    const requestedSessionId = String(sessionId ?? this.activeSessionId ?? '').trim();
    if (!requestedSessionId) {
      return false;
    }

    const changingSession =
      this.activeSessionId !== null && this.activeSessionId !== requestedSessionId;
    if (
      !changingSession &&
      (this.isConnecting ||
        this.socket?.readyState === WebSocket.OPEN ||
        this.socket?.readyState === WebSocket.CONNECTING)
    ) {
      return true;
    }

    this.clearReconnect();
    this.intentionalDisconnect = false;
    this.activeSessionId = requestedSessionId;
    this.isConnecting = true;
    this.reconnectAttempts = changingSession ? 0 : this.reconnectAttempts;
    if (changingSession) {
      this.pendingMessages = [];
      this.replayBuffer = [];
      this.seenEventIds.clear();
      this.seenEventOrder = [];
    }

    const previousSocket = this.socket;
    const generation = ++this.connectionGeneration;
    this.socket = null;
    previousSocket?.close(1000, 'Session changed');

    const socketUrl = new URL(this.url, window.location.href);
    if (socketUrl.protocol === 'http:') socketUrl.protocol = 'ws:';
    if (socketUrl.protocol === 'https:') socketUrl.protocol = 'wss:';
    socketUrl.searchParams.set('session_id', requestedSessionId);
    const socket = new WebSocket(socketUrl.toString());
    this.socket = socket;

    socket.onopen = () => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      this.isConnecting = false;
      this.reconnectAttempts = 0;
      this.clearReconnect();
      this.notifyStatus(true);
      this.flushPendingMessages();
    };

    socket.onmessage = event => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      try {
        this.dispatchIncoming(JSON.parse(event.data));
      } catch (error) {
        console.error('[WebSocketService] Invalid server event:', error);
      }
    };

    socket.onclose = () => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      this.socket = null;
      this.isConnecting = false;
      this.notifyStatus(false);
      if (!this.intentionalDisconnect) {
        this.scheduleReconnect();
      }
    };

    socket.onerror = () => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      socket.close();
    };

    return true;
  }

  disconnect(): void {
    this.intentionalDisconnect = true;
    this.clearReconnect();
    this.connectionGeneration += 1;
    const socket = this.socket;
    this.socket = null;
    this.isConnecting = false;
    this.activeSessionId = null;
    this.pendingMessages = [];
    socket?.close(1000, 'Client disconnected');
    this.notifyStatus(false);
  }

  sendMessage(message: any): boolean {
    if (!this.activeSessionId) {
      return false;
    }

    const claimedSessionId = message?.session_id ?? message?.payload?.session_id;
    if (claimedSessionId && String(claimedSessionId) !== this.activeSessionId) {
      console.error('[WebSocketService] Refused message for a different session');
      return false;
    }

    const scopedMessage = {
      ...message,
      message_id:
        message?.message_id ||
        (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`),
      session_id: this.activeSessionId,
    };
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(scopedMessage));
      return true;
    }

    if (
      this.socket?.readyState === WebSocket.CONNECTING &&
      this.pendingMessages.length < this.pendingMessageLimit
    ) {
      this.pendingMessages.push(scopedMessage);
      return true;
    }
    return false;
  }

  dispatchLocal(data: any): void {
    const sessionId = data?.session_id ?? this.activeSessionId;
    if (
      this.activeSessionId &&
      sessionId &&
      String(sessionId) !== this.activeSessionId
    ) {
      return;
    }
    this.dispatchEvent(sessionId ? { ...data, session_id: String(sessionId) } : data);
  }

  dispatchReplay(events: any[]): void {
    events.forEach(event => this.dispatchLocal({ ...event, replaying: true }));
  }

  subscribe(callback: Listener): () => void {
    this.listeners.add(callback);
    if (this.replayBuffer.length > 0) {
      this.replayBuffer.forEach(event => callback(event));
    }
    return () => this.listeners.delete(callback);
  }

  onStatusChange(callback: (isConnected: boolean) => void): () => void {
    this.statusListeners.add(callback);
    return () => this.statusListeners.delete(callback);
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  getSessionId(): string | null {
    return this.activeSessionId;
  }

  getUrl(): string {
    return this.url;
  }

  startReplayCapture(): number {
    this.replayGeneration += 1;
    this.isReplaying = true;
    this.replayBuffer = [];
    return this.replayGeneration;
  }

  stopReplayCapture(generation?: number): void {
    if (generation !== undefined && generation !== this.replayGeneration) {
      return;
    }
    this.isReplaying = false;
    this.replayBuffer = [];
  }

  private dispatchIncoming(data: any): void {
    if (!this.activeSessionId || String(data?.session_id ?? '') !== this.activeSessionId) {
      console.warn('[WebSocketService] Dropped event outside the active session');
      return;
    }
    this.dispatchEvent(data);
  }

  private dispatchEvent(data: any): void {
    const eventId = data?.event_id ? String(data.event_id) : '';
    if (eventId && this.seenEventIds.has(eventId)) {
      return;
    }
    if (eventId) {
      this.seenEventIds.add(eventId);
      this.seenEventOrder.push(eventId);
      if (this.seenEventOrder.length > this.seenEventLimit) {
        const oldest = this.seenEventOrder.shift();
        if (oldest) this.seenEventIds.delete(oldest);
      }
    }

    if (this.isReplaying && this.replayBuffer.length < this.replayBufferLimit) {
      this.replayBuffer.push(data);
    }
    this.listeners.forEach(callback => callback(data));
  }

  private flushPendingMessages(): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    const pending = this.pendingMessages;
    this.pendingMessages = [];
    pending.forEach(message => this.socket?.send(JSON.stringify(message)));
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout || !this.activeSessionId || this.intentionalDisconnect) return;
    const baseDelay = Math.min(30_000, 1_000 * 2 ** this.reconnectAttempts);
    const delay = Math.round(baseDelay * (0.8 + Math.random() * 0.4));
    this.reconnectAttempts += 1;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  private notifyStatus(isConnected: boolean): void {
    this.statusListeners.forEach(callback => callback(isConnected));
  }
}

const websocketService = new WebSocketService();
export default websocketService;
