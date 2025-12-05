// platform/app/src/utils/WebSocketService.js

class WebSocketService {
  constructor() {
    this.socket = null;
    this.listeners = new Set();
    this.url = "ws://localhost:8150/ws"; // <-- update to your backend WS URL
    this.reconnectInterval = 2000; // 2 seconds
  }

  // Connect to WebSocket
  connect() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return;

    this.socket = new WebSocket(this.url);

    this.socket.onopen = () => {
      console.log("[WS] Connected");
    };

    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.listeners.forEach((cb) => cb(data));
      } catch (err) {
        console.error("[WS] Failed to parse message", err);
      }
    };

    this.socket.onclose = () => {
      console.log("[WS] Disconnected, retrying in 2s...");
      setTimeout(() => this.connect(), this.reconnectInterval);
    };

    this.socket.onerror = (err) => {
      console.error("[WS] Error:", err);
      this.socket.close();
    };
  }

  // Send message to backend
  sendMessage(msg) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(msg));
    } else {
      console.warn("[WS] Socket not open, message not sent:", msg);
    }
  }

  // Subscribe to messages
  subscribe(callback) {
    this.listeners.add(callback);
    // Return unsubscribe function
    return () => this.listeners.delete(callback);
  }
}

// Export a singleton instance
const websocketService = new WebSocketService();
export default websocketService;
