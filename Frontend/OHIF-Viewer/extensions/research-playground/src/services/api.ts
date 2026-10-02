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

/**
 * Deliver user text to a session's planner input queue (WEBSOCKET_FIX_PLAN.md
 * WS-10). Replaces what used to be a `user`/`user_input`/`chat_input` frame
 * sent over the WebSocket -- the socket is push-only now, so this REST call
 * is the only way in. `idempotencyKey` lets a caller retry safely: the
 * server recognizes a repeated key for the same session and skips re-queuing
 * it rather than delivering the text twice.
 */
export async function submitSessionInput(
  sessionId: string,
  text: string,
  idempotencyKey: string
) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/input`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw new Error(`Session input request failed with status ${res.status}`);
  }
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
 * Per-node execution status, read from `node_runs`.
 *
 * This is the durable record: it is the same in a fresh process as in the one
 * that ran the pipeline, which is what makes a reconnect or a restart able to
 * show real progress instead of assuming none.
 */
export async function fetchNodeStatus(sessionId: string) {
  const res = await fetch(`${BACKEND_API}/runs/session/${sessionId}/status`);
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
