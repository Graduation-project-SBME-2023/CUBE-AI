import { getBackendApiBaseUrl, getAgentsApiBaseUrl } from '../../../../utils/apiBase';

export const handleModifyAction = async (uid: string) => {
  const response = await fetch(`${getBackendApiBaseUrl()}/volumes/${uid}/study`);
  if (!response.ok) {
    throw new Error(`Failed to fetch study UID. Status: ${response.status}`);
  }
  return await response.json();
};

export const saveSegmentationReview = async (
  sessionId: string,
  results: { accepted: string[]; rejected: string[]; modified: string[] }
) => {
  const response = await fetch(`${getAgentsApiBaseUrl()}/segmentation-review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      ...results,
      session_id: sessionId,
    }),
  });

  if (!response.ok) {
    throw new Error(`Server responded with status ${response.status}`);
  }
  return response;
};

export const saveSegmentationReviewDraft = async (
  sessionId: string,
  runId: string | undefined,
  decisions: Record<string, 'accepted' | 'rejected' | 'modified'>
) => {
  const response = await fetch(`${getAgentsApiBaseUrl()}/segmentation-review/draft`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      run_id: runId,
      decisions,
    }),
    keepalive: true,
  });

  if (!response.ok) {
    throw new Error(`Server responded with status ${response.status}`);
  }
};

export const addCacheBuster = (url: string, version: number | string = Date.now()) => {
  const parsedUrl = new URL(url, window.location.href);
  parsedUrl.searchParams.set('maskVersion', String(version));
  return parsedUrl.toString();
};
