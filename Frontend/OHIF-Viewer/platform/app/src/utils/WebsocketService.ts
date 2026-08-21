import { getAgentsWsUrl } from './runtimeUrls';

export class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  readonly url = getAgentsWsUrl();

  private activeSessionId: string | null = null;
  private isConnecting = false;
  private intentionalDisconnect = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private connectionGeneration = 0;
  private pendingMessages: any[] = [];
  private seenEventIds: Set<string> = new Set();
  private seenEventOrder: string[] = [];
  private readonly seenEventLimit = 2000;

  connect(sessionId?: string | number | null): boolean {
    const targetSessionId = String(sessionId ?? this.activeSessionId ?? '').trim();
    if (!targetSessionId) return false;

    const changingSession =
      this.activeSessionId !== null && this.activeSessionId !== targetSessionId;
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
    this.activeSessionId = targetSessionId;
    this.isConnecting = true;
    if (changingSession) {
      this.reconnectAttempts = 0;
      this.pendingMessages = [];
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
    socketUrl.searchParams.set('session_id', targetSessionId);
    const socket = new WebSocket(socketUrl.toString());
    this.socket = socket;

    socket.onopen = () => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      this.isConnecting = false;
      this.reconnectAttempts = 0;
      this.clearReconnect();
      this.notifyStatus(true);
      const pending = this.pendingMessages;
      this.pendingMessages = [];
      pending.forEach(message => socket.send(JSON.stringify(message)));
    };

    socket.onmessage = event => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      try {
        const data = JSON.parse(event.data);
        if (String(data?.session_id ?? '') !== this.activeSessionId) return;
        const eventId = data?.event_id ? String(data.event_id) : '';
        if (eventId && this.seenEventIds.has(eventId)) return;
        if (eventId) {
          this.seenEventIds.add(eventId);
          this.seenEventOrder.push(eventId);
          if (this.seenEventOrder.length > this.seenEventLimit) {
            const oldest = this.seenEventOrder.shift();
            if (oldest) this.seenEventIds.delete(oldest);
          }
        }
        this.listeners.forEach(callback => callback(data));
      } catch (error) {
        console.error('[WebSocketService] Invalid server event:', error);
      }
    };

    socket.onclose = () => {
      if (generation !== this.connectionGeneration || socket !== this.socket) return;
      this.socket = null;
      this.isConnecting = false;
      this.notifyStatus(false);
      if (!this.intentionalDisconnect) this.scheduleReconnect();
    };

    socket.onerror = () => {
      if (generation === this.connectionGeneration && socket === this.socket) {
        socket.close();
      }
    };
    return true;
  }

  disconnect(): void {
    this.intentionalDisconnect = true;
    this.clearReconnect();
    this.connectionGeneration += 1;
    const socket = this.socket;
    this.socket = null;
    this.activeSessionId = null;
    this.isConnecting = false;
    this.pendingMessages = [];
    socket?.close(1000, 'Client disconnected');
    this.notifyStatus(false);
  }

  sendMessage(message: any): boolean {
    if (!this.activeSessionId) return false;
    const claimedSessionId = message?.session_id ?? message?.payload?.session_id;
    if (claimedSessionId && String(claimedSessionId) !== this.activeSessionId) return false;
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
    if (this.socket?.readyState === WebSocket.CONNECTING && this.pendingMessages.length < 32) {
      this.pendingMessages.push(scopedMessage);
      return true;
    }
    return false;
  }

  dispatchLocal(data: any): void {
    const sessionId = data?.session_id ?? this.activeSessionId;
    if (this.activeSessionId && String(sessionId ?? '') !== this.activeSessionId) return;
    const event = sessionId ? { ...data, session_id: String(sessionId) } : data;
    this.listeners.forEach(callback => callback(event));
  }

  subscribe(callback: (data: any) => void): () => void {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  onStatusChange(callback: (isConnected: boolean) => void): () => void {
    this.statusListeners.add(callback);
    return () => this.statusListeners.delete(callback);
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
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
