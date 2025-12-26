class WebSocketService {
  socket: WebSocket | null = null;
  listeners: Set<(data: any) => void> = new Set();
  statusListeners: Set<(isConnected: boolean) => void> = new Set();
  url = "ws://localhost:8150/ws";
  reconnectInterval = 2000;

  connect() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return;
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => {
      this.notifyStatus(true);
    };
    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.listeners.forEach((cb) => cb(data));
      } catch (err) {
        // handle error
      }
    };
    this.socket.onclose = () => {
      this.notifyStatus(false);
      setTimeout(() => this.connect(), this.reconnectInterval);
    };
    this.socket.onerror = () => {
      this.notifyStatus(false);
      this.socket?.close();
    };
  }
  sendMessage(msg: any) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(msg));
    }
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
