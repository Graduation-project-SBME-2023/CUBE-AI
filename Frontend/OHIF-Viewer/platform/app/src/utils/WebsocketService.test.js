import { WebSocketService } from './WebsocketService';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.sent = [];
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  receive(data) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  send(message) {
    this.sent.push(JSON.parse(message));
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

describe('WebSocketService session isolation', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    global.WebSocket = MockWebSocket;
  });

  test('binds the connection and outgoing messages to one session', () => {
    const service = new WebSocketService();
    expect(service.connect('session-a')).toBe(true);

    const socket = MockWebSocket.instances[0];
    expect(new URL(socket.url).searchParams.get('session_id')).toBe('session-a');
    socket.open();

    expect(service.sendMessage({ type: 'user', payload: 'hello' })).toBe(true);
    expect(socket.sent[0].session_id).toBe('session-a');
    expect(socket.sent[0].message_id).toEqual(expect.any(String));
    expect(service.sendMessage({ type: 'user', session_id: 'session-b' })).toBe(false);
  });

  test('drops events from other sessions and deduplicates event ids', () => {
    const service = new WebSocketService();
    const received = [];
    service.subscribe(event => received.push(event));
    service.connect('session-a');
    const socket = MockWebSocket.instances[0];
    socket.open();

    socket.receive({ event_id: 'b-1', session_id: 'session-b', type: 'status' });
    socket.receive({ event_id: 'a-1', session_id: 'session-a', type: 'status' });
    socket.receive({ event_id: 'a-1', session_id: 'session-a', type: 'status' });

    expect(received).toHaveLength(1);
    expect(received[0].session_id).toBe('session-a');
  });

  test('does not reconnect after an intentional disconnect', () => {
    jest.useFakeTimers();
    const service = new WebSocketService();
    service.connect('session-a');
    MockWebSocket.instances[0].open();

    service.disconnect();
    jest.runOnlyPendingTimers();

    expect(MockWebSocket.instances).toHaveLength(1);
    jest.useRealTimers();
  });

  test('ignores events from a socket replaced during a session switch', () => {
    const service = new WebSocketService();
    const received = [];
    service.subscribe(event => received.push(event));

    service.connect('session-a');
    const firstSocket = MockWebSocket.instances[0];
    firstSocket.open();
    service.connect('session-b');
    const secondSocket = MockWebSocket.instances[1];
    secondSocket.open();

    firstSocket.receive({ event_id: 'stale', session_id: 'session-a' });
    secondSocket.receive({ event_id: 'current', session_id: 'session-b' });

    expect(received.map(event => event.event_id)).toEqual(['current']);
  });
});
