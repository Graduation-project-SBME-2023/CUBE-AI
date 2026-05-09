const API_BASE = 'http://localhost:8150';

export async function fetchSessionPlanState(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/plan-state`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchSessionExecutionState(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/execution-state`);
  if (!res.ok) return null;
  return res.json();
}

export async function replaySessionLogs(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/replay-logs`);
  return res.ok;
}
