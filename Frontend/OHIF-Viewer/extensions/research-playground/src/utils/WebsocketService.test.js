import websocketService from './WebsocketService';

// Regression test for the "pipeline disappears on session re-entry" bug:
// the websocket service is a page-lifetime singleton, so re-entering a
// session already visited in this tab must not have its events suppressed
// by bookkeeping left over from the earlier mount.
describe('WebsocketService session-scoped replay bookkeeping', () => {
  afterEach(() => {
    websocketService.resetSession('session-a');
    websocketService.resetSession('session-b');
  });

  it('delivers an event once per session and advances that session cursor', () => {
    const delivered = websocketService.trackEvent({ event_id: 1, session_id: 'session-a' });
    expect(delivered).toBe(true);
    expect(websocketService.replayCursor('session-a')).toBe(1);

    const duplicate = websocketService.trackEvent({ event_id: 1, session_id: 'session-a' });
    expect(duplicate).toBe(false);
  });

  it('does not let one session leak dedup state into another', () => {
    websocketService.trackEvent({ event_id: 5, session_id: 'session-a' });
    const deliveredToOther = websocketService.trackEvent({ event_id: 5, session_id: 'session-b' });
    expect(deliveredToOther).toBe(true);
  });

  it('resetSession clears the cursor and re-admits previously delivered event ids', () => {
    websocketService.trackEvent({ event_id: 42, session_id: 'session-a' });
    expect(websocketService.replayCursor('session-a')).toBe(42);

    websocketService.resetSession('session-a');
    expect(websocketService.replayCursor('session-a')).toBe(0);

    // A fresh mount replaying from cursor 0 must be able to receive event 42
    // again — this is exactly what a full-history replay resends.
    const redelivered = websocketService.trackEvent({ event_id: 42, session_id: 'session-a' });
    expect(redelivered).toBe(true);
  });
});
