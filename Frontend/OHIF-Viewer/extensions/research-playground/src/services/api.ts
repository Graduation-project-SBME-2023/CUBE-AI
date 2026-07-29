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

export async function replaySessionLogs(sessionId: string) {
  const res = await fetch(`${API_BASE}/session/${sessionId}/replay-logs`);
  return res.ok;
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
