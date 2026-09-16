import { AGENTS_API, BACKEND_API } from '../config';

const API_BASE = AGENTS_API;

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

export async function fetchSessionStatus(sessionId: string) {
  const res = await fetch(`${API_BASE}/status/${sessionId}`);
  if (!res.ok) return null;
  return res.json();
}

export async function hydrateSession(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/recover`, {
    method: 'POST',
  });
  if (!res.ok) return null;
  return res.json();
}

export async function resumeSessionPipeline(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/resume-pipeline`, {
    method: 'POST',
  });
  if (!res.ok) {
    const error = await res.json().catch(() => null);
    throw new Error(error?.detail || `Resume request failed with status ${res.status}`);
  }
  return res.json();
}

/**
 * Replay a session's events from a cursor.
 *
 * `afterEventId` is the last `ws_log.event_id` this client actually received,
 * which the websocket service tracks. Replaying from 0 re-delivers the whole
 * session; replaying from the cursor delivers only what was missed.
 */
export async function replaySessionLogs(sessionId: string, afterEventId = 0) {
  const query = afterEventId > 0 ? `?after_event_id=${afterEventId}` : '';
  const res = await fetch(`${API_BASE}/session/${sessionId}/replay-logs${query}`);
  return res.ok;
}

/**
 * Per-step execution status, read from `agent_runs`.
 *
 * This is the durable record: it is the same in a fresh process as in the one
 * that ran the pipeline, which is what makes a reconnect or a restart able to
 * show real progress instead of assuming none.
 */
export async function fetchAgentRunStatus(sessionId: string) {
  const res = await fetch(`${BACKEND_API}/agent-runs/session/${sessionId}/status`);
  if (!res.ok) return null;
  return res.json();
}

export async function compileLatexReport(
  sessionId: string,
  latexText: string,
  outputFilename = 'final_report.pdf'
) {
  const res = await fetch(`${API_BASE}/reports/compile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      latex_text: latexText,
      output_filename: outputFilename,
    }),
  });

  if (!res.ok) {
    throw new Error(`Report compile request failed with status ${res.status}`);
  }

  return res.json();
}
