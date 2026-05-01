class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  url = "ws://localhost:8150/ws";
  reconnectInterval = 2000;
  private isConnecting = false;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private replayBuffer: any[] = [];
  private isReplaying = false;
  private replayBufferLimit = 500; // safety cap — avoid unbounded memory

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
    return () => this.listeners.delete(callback)
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