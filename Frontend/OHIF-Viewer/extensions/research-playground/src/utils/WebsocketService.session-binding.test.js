// A minimal stand-in for the browser WebSocket API. Real sockets open/close
// asynchronously over the network; this fakes that with explicit test-only
// hooks (_open/_receive) so the session-binding logic in WebsocketService.ts
// can be driven deterministically (WEBSOCKET_FIX_PLAN.md Principle 2/WS-03).
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    if (this.onclose) this.onclose({});
  }

  _open() {
    this.readyState = FakeWebSocket.OPEN;
    if (this.onopen) this.onopen({});
  }

  _receive(data) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(data) });
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];

describe('WebsocketService session binding (WEBSOCKET_FIX_PLAN.md Principle 2 / WS-03)', () => {
  let originalWebSocket;
  let websocketService;

  // websocketService is a page-lifetime singleton by design (see
  // WebsocketService.ts). That's real behavior the "same session cursor
  // survives a remount" tests rely on, but it means one test's connect()
  // leaves sessionId set for the next one -- so each test here gets its own
  // fresh module instance instead of sharing state through the import cache.
  beforeEach(() => {
    FakeWebSocket.instances = [];
    originalWebSocket = global.WebSocket;
    global.WebSocket = FakeWebSocket;
    jest.resetModules();
    websocketService = require('./WebsocketService').default;
  });

  afterEach(() => {
    websocketService.disconnect();
    global.WebSocket = originalWebSocket;
  });

  it('opens with session_id in the URL', () => {
    websocketService.connect('session-a');

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain('session_id=session-a');
  });

  it('a fresh session connects with after_event_id=0 (WS-08/WS-09)', () => {
    websocketService.connect('session-a');

    expect(FakeWebSocket.instances[0].url).toContain('after_event_id=0');
  });

  it('reconnecting passes the stored replay cursor as after_event_id', () => {
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();
    socket._receive({ type: 'msg', payload: {}, session_id: 'session-a', event_id: 7 });

    // A genuine drop, not an intentional close -- reconnect must resume
    // from the cursor this tab actually reached, not replay from scratch.
    socket.close();
    websocketService.connect('session-a');

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1].url).toContain('after_event_id=7');
  });

  it('a bare connect() with no session yet is a no-op', () => {
    websocketService.connect();

    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('closes the old socket and opens a new one when the session changes', () => {
    websocketService.connect('session-a');
    FakeWebSocket.instances[0]._open();

    websocketService.connect('session-b');

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[0].readyState).toBe(FakeWebSocket.CLOSED);
    expect(FakeWebSocket.instances[1].url).toContain('session_id=session-b');
  });

  it('a second connect() for the same session does not open another socket', () => {
    websocketService.connect('session-a');
    FakeWebSocket.instances[0]._open();

    websocketService.connect('session-a');

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('drops a frame with no session_id and a frame for a foreign session (W17)', () => {
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();
    const received = [];
    const unsubscribe = websocketService.subscribe(data => received.push(data));

    socket._receive({ type: 'tool_progress', payload: {} }); // missing session_id
    socket._receive({ type: 'tool_progress', payload: {}, session_id: 'session-b' }); // foreign
    socket._receive({ type: 'tool_progress', payload: {}, session_id: 'session-a' }); // own

    unsubscribe();
    expect(received).toHaveLength(1);
    expect(received[0].session_id).toBe('session-a');
  });

  it('an intentional session switch never reports a disconnect to status listeners', () => {
    const statuses = [];
    const unsubscribe = websocketService.onStatusChange(status => statuses.push(status));

    websocketService.connect('session-a');
    FakeWebSocket.instances[0]._open();
    statuses.length = 0; // drop the initial "connected" notification

    websocketService.connect('session-b'); // closes session-a's socket intentionally
    FakeWebSocket.instances[1]._open();

    unsubscribe();
    expect(statuses).not.toContain(false);
    expect(statuses).toContain(true);
  });

  it('a genuine drop (server-initiated close) still reports disconnected', () => {
    const statuses = [];
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();
    const unsubscribe = websocketService.onStatusChange(status => statuses.push(status));

    socket.close(); // not via disconnect() -- an unrequested close, e.g. server/network drop

    unsubscribe();
    expect(statuses).toContain(false);
  });
});
