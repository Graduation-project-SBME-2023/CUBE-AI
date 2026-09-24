// Isolated in its own file (not WebsocketService.session-binding.test.js)
// because it needs fake timers for the whole describe block, and mocking
// Math.random for deterministic jitter -- neither should leak into the
// other test file's synchronous, real-timer assertions.

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

  _receiveRaw(text) {
    if (this.onmessage) this.onmessage({ data: text });
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];

// Pulls out the delay passed to the reconnect's own setTimeout call, i.e.
// the one with a positive, finite ms argument -- ignores any zero-delay
// timers other code in the module might also schedule.
const lastReconnectDelay = setTimeoutSpy => {
  const call = setTimeoutSpy.mock.calls
    .slice()
    .reverse()
    .find(([, ms]) => typeof ms === 'number' && ms > 0);
  return call ? call[1] : undefined;
};

describe('WebsocketService reconnect backoff (WEBSOCKET_FIX_PLAN.md WS-12)', () => {
  let originalWebSocket;
  let websocketService;
  let randomSpy;
  let setTimeoutSpy;

  beforeEach(() => {
    jest.useFakeTimers();
    FakeWebSocket.instances = [];
    originalWebSocket = global.WebSocket;
    global.WebSocket = FakeWebSocket;
    jest.resetModules();
    websocketService = require('./WebsocketService').default;
    // Full jitter picks uniformly in [0, cap]; pinning Math.random to 1
    // makes every draw land exactly on the cap, so the growth sequence is
    // deterministic without weakening what's being asserted (the cap itself
    // still has to double each time and still has to saturate at 30s).
    randomSpy = jest.spyOn(Math, 'random').mockReturnValue(1);
    setTimeoutSpy = jest.spyOn(global, 'setTimeout');
  });

  afterEach(() => {
    websocketService.disconnect();
    global.WebSocket = originalWebSocket;
    randomSpy.mockRestore();
    setTimeoutSpy.mockRestore();
    jest.useRealTimers();
  });

  it('doubles the reconnect delay per consecutive failed attempt, capped at 30s', () => {
    websocketService.connect('session-a');
    let socket = FakeWebSocket.instances[0];
    socket._open();

    const delays = [];
    for (let i = 0; i < 6; i++) {
      setTimeoutSpy.mockClear();
      socket.close(); // a genuine drop -- schedules a reconnect
      const delay = lastReconnectDelay(setTimeoutSpy);
      delays.push(delay);
      jest.advanceTimersByTime(delay);
      socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
      // Still down: the freshly (re)connected socket drops again before it
      // ever opens, so the next iteration measures the next backoff step.
    }

    expect(delays).toEqual([1000, 2000, 4000, 8000, 16000, 30000]);
  });

  it('resets the backoff to the base delay after a successful reconnect', () => {
    websocketService.connect('session-a');
    let socket = FakeWebSocket.instances[0];
    socket._open();

    socket.close();
    expect(lastReconnectDelay(setTimeoutSpy)).toBe(1000);
    jest.advanceTimersByTime(1000);

    const reconnected = FakeWebSocket.instances[1];
    reconnected._open(); // success -- backoff must start over from here

    setTimeoutSpy.mockClear();
    reconnected.close();
    expect(lastReconnectDelay(setTimeoutSpy)).toBe(1000);
  });

  it('jitters the delay rather than always waiting the full cap', () => {
    randomSpy.mockReturnValue(0.5);

    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();

    socket.close();
    // cap for the first attempt is 1000ms; at random()=0.5 the delay is half that.
    expect(lastReconnectDelay(setTimeoutSpy)).toBe(500);
  });

  it('does not schedule a second reconnect while one is already pending', () => {
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();

    socket.close();
    setTimeoutSpy.mockClear();
    // Nothing else drops or opens in between -- a second call while the
    // timer is still pending must not schedule another one.
    websocketService.connect(); // e.g. some other consumer's defensive connect()

    expect(setTimeoutSpy).not.toHaveBeenCalled();
  });
});

describe('WebsocketService surfaces parse errors (W21)', () => {
  let originalWebSocket;
  let websocketService;

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

  it('notifies error listeners when a frame fails to parse, instead of only logging it', () => {
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();

    const errors = [];
    const unsubscribe = websocketService.onError(message => errors.push(message));

    socket._receiveRaw('{not valid json');

    unsubscribe();
    expect(errors).toHaveLength(1);
    expect(errors[0]).toEqual(expect.any(String));
  });

  it('does not notify error listeners for a well-formed frame', () => {
    websocketService.connect('session-a');
    const socket = FakeWebSocket.instances[0];
    socket._open();

    const errors = [];
    const unsubscribe = websocketService.onError(message => errors.push(message));

    socket._receive({ type: 'tool_progress', payload: {}, session_id: 'session-a' });

    unsubscribe();
    expect(errors).toHaveLength(0);
  });
});
