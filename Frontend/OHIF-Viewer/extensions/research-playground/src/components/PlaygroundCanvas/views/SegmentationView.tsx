import React, { useState, useCallback, useEffect } from 'react';
import { Check, X, CheckSquare, XSquare, Save, Loader2, Edit } from 'lucide-react';
import { useSnackbar } from '@ohif/ui';
import {
  FailedVolume,
  SegmentationProgress,
  VolumeGif,
  ReviewContainerProps,
  ReviewStatus,
} from './segmentation/types';
import { InfoPanel } from './segmentation/Shared';
import EmbeddedSegmentationModal, {
  SegmentationSubmitPayload,
} from './segmentation/EmbeddedSegmentationModal';
import {
  addCacheBuster,
  handleModifyAction,
  saveSegmentationReview,
  saveSegmentationReviewDraft,
} from './segmentation/utils';

const isSavedReviewChoice = (value: unknown): value is ReviewStatus =>
  value === 'accepted' || value === 'rejected' || value === 'modified';

const getReviewDraftStorageKey = (sessionId?: string, runId?: string) =>
  sessionId
    ? `cube_segmentation_review_${encodeURIComponent(sessionId)}_${encodeURIComponent(
        runId || 'legacy'
      )}`
    : null;

const getReviewSubmittedStorageKey = (sessionId?: string, runId?: string) =>
  sessionId
    ? `cube_segmentation_submitted_${encodeURIComponent(sessionId)}_${encodeURIComponent(
        runId || 'legacy'
      )}`
    : null;

const loadReviewSubmitted = (sessionId?: string, runId?: string): boolean => {
  const key = getReviewSubmittedStorageKey(sessionId, runId);
  if (!key || typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(key) === 'true';
  } catch (_) {
    return false;
  }
};

const persistReviewSubmitted = (sessionId?: string, runId?: string) => {
  const key = getReviewSubmittedStorageKey(sessionId, runId);
  if (!key || typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(key, 'true');
  } catch (_) {
    // Silently ignore storage errors.
  }
};

const loadReviewDraft = (storageKey: string | null): Record<string, ReviewStatus> => {
  if (!storageKey || typeof window === 'undefined') {
    return {};
  }

  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || '{}');
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(saved).filter(([, status]) => isSavedReviewChoice(status))
    ) as Record<string, ReviewStatus>;
  } catch (_) {
    return {};
  }
};

const loadReviewDraftForRun = (sessionId?: string, runId?: string) => {
  const storageKey = getReviewDraftStorageKey(sessionId, runId);
  const currentDraft = loadReviewDraft(storageKey);
  if (Object.keys(currentDraft).length > 0 || !sessionId || !runId) {
    return currentDraft;
  }

  // Sessions created before run-aware drafts used the legacy session-only key.
  // Migrate it once so replaying an older hypothesis cannot drop the choice
  // when the reconstructed manifest introduces its run ID.
  const legacyStorageKey = getReviewDraftStorageKey(sessionId);
  const legacyDraft = loadReviewDraft(legacyStorageKey);
  if (Object.keys(legacyDraft).length > 0 && storageKey && typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(legacyDraft));
      if (legacyStorageKey) {
        window.localStorage.removeItem(legacyStorageKey);
      }
    } catch (_) {
      // Continue with the in-memory migration when storage is unavailable.
    }
  }
  return legacyDraft;
};

const buildReviewStatus = (
  volumes: VolumeGif[],
  draft: Record<string, ReviewStatus>
): Record<string, ReviewStatus> => {
  const next: Record<string, ReviewStatus> = {};
  volumes.forEach(volume => {
    const reviewKey = volume.pairId || volume.seriesInstanceUid;
    // The draft is the user's newest choice. A replayed manifest can still
    // contain the older accepted value until the review is consumed, or
    // forever for an already-completed historical hypothesis.
    // Default is 'accepted'. Only explicit user choices (rejected / modified /
    // re-accepted) are stored in the draft. If a volume has no draft entry it
    // was never touched, so it correctly stays 'accepted'.
    next[reviewKey] = isSavedReviewChoice(draft[reviewKey])
      ? draft[reviewKey]
      : isSavedReviewChoice(volume.reviewStatus)
        ? volume.reviewStatus
        : 'accepted';
  });
  return next;
};

const getSavedReviewChoices = (reviewStatus: Record<string, ReviewStatus>) =>
  Object.fromEntries(
    Object.entries(reviewStatus).filter(([, status]) => isSavedReviewChoice(status))
  ) as Record<string, 'accepted' | 'rejected' | 'modified'>;

// ─── Review View ──────────────────────────────────────────────────────────────

function ReviewView({
  targetRoi,
  selectedModel,
  volumes,
  failedVolumes,
  progress,
  onSave,
  isLoading,
  isFinished,
  sessionId,
  runId,
  onVolumeUpdated,
}: {
  targetRoi: string;
  selectedModel: string;
  volumes: VolumeGif[];
  failedVolumes: FailedVolume[];
  progress?: SegmentationProgress;
  onSave: (results: { accepted: string[]; rejected: string[]; modified: string[] }) => Promise<boolean>;
  isLoading: boolean;
  isFinished: boolean;
  sessionId?: string;
  runId?: string;
  onVolumeUpdated: (pairId: string, updates: Partial<VolumeGif>) => void;
}) {
  const reviewDraftStorageKey = getReviewDraftStorageKey(sessionId, runId);
  const [reviewStatus, setReviewStatus] = useState<Record<string, ReviewStatus>>(() =>
    buildReviewStatus(volumes, loadReviewDraftForRun(sessionId, runId))
  );
  const [openingPairId, setOpeningPairId] = useState<string | null>(null);
  const [previewRefresh, setPreviewRefresh] = useState<Record<string, 'loading' | 'error'>>({});
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'submitted'>(
    () => (loadReviewSubmitted(sessionId, runId) ? 'submitted' : 'idle')
  );
  const [editor, setEditor] = useState<{
    volume: VolumeGif;
    reviewKey: string;
    viewerUrl: string;
  } | null>(null);

  const snackbar = useSnackbar();

  useEffect(() => {
    // Re-read the persisted draft from localStorage every time the volumes
    // list changes (e.g., new pairs streamed in, or component remounted after
    // the user exits and re-enters a hypothesis). Using the localStorage draft
    // as the authoritative source — rather than just the in-memory `prev` —
    // guarantees that explicit reject/modify choices survive a remount even
    // when the component state is freshly initialised.
    const persistedDraft = loadReviewDraftForRun(sessionId, runId);
    setReviewStatus(prev => {
      // Merge: persisted draft wins > in-memory state > volume.reviewStatus > 'accepted' (default)
      const mergedDraft: Record<string, ReviewStatus> = { ...prev };
      Object.entries(persistedDraft).forEach(([key, status]) => {
        if (isSavedReviewChoice(status)) {
          mergedDraft[key] = status;
        }
      });
      const next = buildReviewStatus(volumes, mergedDraft);
      const previousKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      const unchanged =
        previousKeys.length === nextKeys.length &&
        nextKeys.every(key => prev[key] === next[key]);
      return unchanged ? prev : next;
    });
  }, [volumes, sessionId, runId]);

  useEffect(() => {
    if (
      !reviewDraftStorageKey ||
      typeof window === 'undefined' ||
      Object.keys(reviewStatus).length === 0
    ) {
      return;
    }

    try {
      window.localStorage.setItem(reviewDraftStorageKey, JSON.stringify(reviewStatus));
    } catch (_) {
      // Browser storage is a resilience layer; review controls must still work
      // when storage is unavailable or full.
    }
  }, [reviewDraftStorageKey, reviewStatus]);

  useEffect(() => {
    // When the session changes, only reset if the new session hasn't been submitted.
    setSaveState(loadReviewSubmitted(sessionId, runId) ? 'submitted' : 'idle');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, runId]);

  useEffect(() => {
    if (!isFinished) {
      // Only reset to idle when not yet submitted for this session.
      setSaveState(prev => (prev === 'submitted' ? 'submitted' : 'idle'));
    }
  }, [isFinished]);

  const isReviewLocked = !isFinished || saveState !== 'idle';

  const persistReviewDraft = useCallback(
    (next: Record<string, ReviewStatus>) => {
      if (!sessionId) {
        return;
      }
      void saveSegmentationReviewDraft(sessionId, runId, getSavedReviewChoices(next)).catch(
        error => console.error('Failed to persist segmentation review draft:', error)
      );
    },
    [runId, sessionId]
  );

  const handleToggle = useCallback(
    (uid: string, status: 'accepted' | 'rejected' | 'modified') => {
      const next = { ...reviewStatus, [uid]: status };
      setReviewStatus(next);
      persistReviewDraft(next);
    },
    [persistReviewDraft, reviewStatus]
  );

  const handleAcceptAll = useCallback(() => {
    const s: Record<string, ReviewStatus> = {};
    volumes.forEach(v => {
      s[v.pairId || v.seriesInstanceUid] = 'accepted';
    });
    setReviewStatus(s);
    persistReviewDraft(s);
  }, [persistReviewDraft, volumes]);

  const handleRejectAll = useCallback(() => {
    const s: Record<string, ReviewStatus> = {};
    volumes.forEach(v => {
      s[v.pairId || v.seriesInstanceUid] = 'rejected';
    });
    setReviewStatus(s);
    persistReviewDraft(s);
  }, [persistReviewDraft, volumes]);

  const handleModify = async (volume: VolumeGif) => {
    const uid = volume.seriesInstanceUid;
    const reviewKey = volume.pairId || uid;
    if (!sessionId) {
      snackbar.show({
        title: 'Session Error',
        message: 'Session ID is missing. Unable to open the segmentation editor.',
        type: 'error',
        duration: 5000,
      });
      return;
    }

    setOpeningPairId(reviewKey);
    try {
      const data = await handleModifyAction(uid);

      if (data.study_uid) {
        const params = new URLSearchParams({
          StudyInstanceUIDs: data.study_uid,
          SeriesInstanceUID: uid,
          session_id: sessionId,
          series_uid: uid,
          target_id: volume.targetId || '',
          pair_id: reviewKey,
          target_label: volume.targetLabel || '',
          embedded: '1',
        });
        setEditor({
          volume,
          reviewKey,
          viewerUrl: `${window.location.origin}/monai-label?${params.toString()}`,
        });
      } else {
        console.error('study_uid not found in the response body', data);
        snackbar.show({
          title: 'Error',
          message: 'Study UID missing from the server response.',
          type: 'error',
          duration: 5000,
        });
      }
    } catch (error) {
      console.error('Error retrieving study uid:', error);
      snackbar.show({
        title: 'Connection Error',
        message: 'Failed to connect to the backend to retrieve the Study UID.',
        type: 'error',
        duration: 5000,
      });
    } finally {
      setOpeningPairId(null);
    }
  };

  const handleEditorSubmitted = useCallback(
    (payload: SegmentationSubmitPayload) => {
      if (
        !editor ||
        payload.pairId !== editor.reviewKey ||
        payload.seriesInstanceUid !== editor.volume.seriesInstanceUid ||
        payload.sessionId !== sessionId
      ) {
        console.warn('Ignoring segmentation update for a different scan.', payload);
        return;
      }

      const reviewKey = editor.reviewKey;
      const refreshedUrl = addCacheBuster(
        payload.overlay?.gifUrl || editor.volume.gifUrl,
        payload.overlay?.updatedAt
      );

      setPreviewRefresh(prev => ({ ...prev, [reviewKey]: 'loading' }));
      onVolumeUpdated(reviewKey, {
        gifUrl: refreshedUrl,
        isEmptyMask: payload.overlay?.isEmptyMask ?? false,
      });
      handleToggle(reviewKey, 'modified');
      setEditor(null);
    },
    [editor, handleToggle, onVolumeUpdated, sessionId]
  );

  const handlePreviewLoaded = useCallback((reviewKey: string) => {
    setPreviewRefresh(prev => {
      if (!prev[reviewKey]) {
        return prev;
      }
      const next = { ...prev };
      delete next[reviewKey];
      return next;
    });
  }, []);

  const handlePreviewError = useCallback((reviewKey: string) => {
    setPreviewRefresh(prev => ({ ...prev, [reviewKey]: 'error' }));
  }, []);

  const retryPreview = useCallback(
    (volume: VolumeGif, reviewKey: string) => {
      setPreviewRefresh(prev => ({ ...prev, [reviewKey]: 'loading' }));
      onVolumeUpdated(reviewKey, {
        gifUrl: addCacheBuster(volume.gifUrl),
      });
    },
    [onVolumeUpdated]
  );

  const handleSave = async () => {
    if (isReviewLocked) {
      return;
    }
    const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
    const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
    const modified = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'modified');
    setSaveState('saving');
    const saved = await onSave({ accepted, rejected, modified });
    if (saved) {
      persistReviewSubmitted(sessionId, runId);
      setSaveState('submitted');
    } else {
      setSaveState('idle');
    }
  };

  const acceptedCount = Object.values(reviewStatus).filter(s => s === 'accepted').length;
  const rejectedCount = Object.values(reviewStatus).filter(s => s === 'rejected').length;
  const modifiedCount = Object.values(reviewStatus).filter(s => s === 'modified').length;
  const pendingCount = Object.values(reviewStatus).filter(s => s === 'pending').length;
  const totalCount = progress?.total ?? volumes.length + failedVolumes.length;
  const generatedCount = progress?.generated ?? volumes.length;
  const failedCount = progress?.failed ?? failedVolumes.length;

  return (
    <div className="flex h-full w-full flex-col gap-4 overflow-hidden bg-[#000000] p-4">
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        {/* ── Top Panel ── */}
        <InfoPanel
          targetRoi={targetRoi}
          selectedModel={selectedModel}
        />
        {/* ── GIF Grid ── */}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[#1f2d40] bg-[#151a1f] p-4">
          {/* Bulk actions + counters */}
          <div className="mb-4 flex shrink-0 items-center gap-3 border-b border-[#1f2d40]/50 pb-4">
            <button
              onClick={handleRejectAll}
              disabled={volumes.length === 0 || isLoading || isReviewLocked}
              className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-[#1a1515] px-4 py-2 text-sm text-red-400 transition-colors hover:bg-red-900/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <XSquare size={15} /> Reject All
            </button>
            <button
              onClick={handleAcceptAll}
              disabled={volumes.length === 0 || isLoading || isReviewLocked}
              className="flex items-center gap-2 rounded-lg border border-[#6fe5b8]/30 bg-[#151f1a] px-4 py-2 text-sm text-[#6fe5b8] transition-colors hover:bg-[#6fe5b8]/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CheckSquare size={15} /> Accept All
            </button>

            {volumes.length > 0 && (
              <div className="ml-auto flex items-center gap-4 text-[10px]">
                <span className="text-[#8aa5c0]">
                  {generatedCount}/{totalCount} overlays
                </span>
                <span className="flex items-center gap-1 text-[#6fe5b8]">
                  <CheckSquare size={11} /> {acceptedCount} accepted
                </span>
                <span className="flex items-center gap-1 text-red-400">
                  <XSquare size={11} /> {rejectedCount} rejected
                </span>
                <span className="flex items-center gap-1 text-[#eab308]">
                  <Edit size={11} /> {modifiedCount} modified
                </span>
                <span className="text-[#566a84]">{pendingCount} pending</span>
              </div>
            )}
          </div>

          {/* Loading overlay */}
          {isLoading && volumes.length === 0 && (
            <div className="absolute inset-0 top-[73px] z-20 flex flex-col items-center justify-center bg-[#151a1f]/90 backdrop-blur-sm">
              <div className="flex max-w-md flex-col items-center rounded-2xl border border-[#1f2d40] bg-[#0b1015] p-8 text-center shadow-2xl">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-[#3c5d80]/20">
                  <Loader2
                    size={32}
                    className="animate-spin text-[#5acce6]"
                  />
                </div>
                <h3 className="mb-2 text-xl font-bold text-white">Preparing Segmentation</h3>
                <p className="text-sm leading-relaxed text-[#566a84]">
                  Existing and newly generated previews will appear here one pair at a time.
                </p>
              </div>
            </div>
          )}

          {/* GIF Grid */}
          <div className="ohif-scrollbar flex-1 overflow-y-auto">
            <div className="grid grid-cols-2 gap-4 pb-4 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {volumes.map(volume => {
                const reviewKey = volume.pairId || volume.seriesInstanceUid;
                const status = reviewStatus[reviewKey];
                const isAccepted = status === 'accepted';
                const isRejected = status === 'rejected';
                const isModified = status === 'modified';

                return (
                  <div
                    key={reviewKey}
                    style={{
                      height: '220px',
                      display: 'flex',
                      flexDirection: 'column',
                      borderRadius: '12px',
                      overflow: 'hidden',
                      border: `2px solid ${isAccepted ? '#6fe5b8' : isRejected ? '#ef4444' : isModified ? '#eab308' : 'rgba(31,45,64,0.8)'}`,
                      background: '#0b1015',
                      transition: 'border-color 0.2s',
                    }}
                  >
                    {/* Card header */}
                    <div
                      style={{
                        height: '36px',
                        flexShrink: 0,
                        background: 'rgba(0,0,0,0.8)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '0 8px',
                        borderBottom: '1px solid rgba(31,45,64,0.5)',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '10px',
                          color: '#d1d5db',
                          fontFamily: 'monospace',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          maxWidth: '60%',
                        }}
                      >
                        {volume.seriesInstanceUid.slice(-8)}
                        {volume.targetLabel ? ` · ${volume.targetLabel}` : ''}
                      </span>
                      <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                        <button
                          onClick={() => handleModify(volume)}
                          disabled={isReviewLocked || openingPairId === reviewKey}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor:
                              !isReviewLocked && openingPairId !== reviewKey
                                ? 'pointer'
                                : 'not-allowed',
                            background: isModified ? '#eab308' : 'transparent',
                            color: isModified ? '#000' : '#9ca3af',
                            opacity: isReviewLocked ? 0.5 : 1,
                          }}
                          title="Modify Interactively"
                        >
                          {openingPairId === reviewKey ? (
                            <Loader2
                              size={14}
                              className="animate-spin"
                            />
                          ) : (
                            <Edit size={14} />
                          )}
                        </button>
                        <button
                          onClick={() => handleToggle(reviewKey, 'rejected')}
                          disabled={isReviewLocked}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor: isReviewLocked ? 'not-allowed' : 'pointer',
                            background: isRejected ? '#ef4444' : 'transparent',
                            color: isRejected ? '#fff' : '#9ca3af',
                            opacity: isReviewLocked ? 0.5 : 1,
                          }}
                        >
                          <X size={14} />
                        </button>
                        <button
                          onClick={() => handleToggle(reviewKey, 'accepted')}
                          disabled={isReviewLocked}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor: isReviewLocked ? 'not-allowed' : 'pointer',
                            background: isAccepted ? '#6fe5b8' : 'transparent',
                            color: isAccepted ? '#000' : '#9ca3af',
                            opacity: isReviewLocked ? 0.5 : 1,
                          }}
                        >
                          <Check
                            size={14}
                            strokeWidth={3}
                          />
                        </button>
                      </div>
                    </div>

                    {/* GIF */}
                    <div
                      style={{
                        flex: 1,
                        background: '#000',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        overflow: 'hidden',
                        position: 'relative',
                      }}
                    >
                      {previewRefresh[reviewKey] === 'error' ? (
                        <button
                          type="button"
                          onClick={() => retryPreview(volume, reviewKey)}
                          className="flex flex-col items-center gap-2 px-4 text-center text-xs text-red-300"
                        >
                          Preview refresh failed
                          <span className="text-[#5acce6] underline">Retry</span>
                        </button>
                      ) : (
                        <>
                          <img
                            key={volume.gifUrl}
                            src={volume.gifUrl}
                            alt={volume.seriesInstanceUid.slice(-8)}
                            onLoad={() => handlePreviewLoaded(reviewKey)}
                            onError={() => handlePreviewError(reviewKey)}
                            style={{
                              width: '100%',
                              height: '100%',
                              objectFit: 'contain',
                              display: 'block',
                              opacity: previewRefresh[reviewKey] === 'loading' ? 0 : 1,
                            }}
                          />
                          {previewRefresh[reviewKey] === 'loading' && (
                            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black text-xs text-[#8aa5c0]">
                              <Loader2
                                size={18}
                                className="animate-spin text-[#5acce6]"
                              />
                              Refreshing preview…
                            </div>
                          )}
                        </>
                      )}
                      {volume.isEmptyMask && (
                        <div
                          style={{
                            position: 'absolute',
                            top: '6px',
                            left: '6px',
                            background: 'rgba(234, 179, 8, 0.85)',
                            color: '#000',
                            fontSize: '9px',
                            fontWeight: 'bold',
                            padding: '2px 6px',
                            borderRadius: '4px',
                            textTransform: 'uppercase',
                            letterSpacing: '0.5px',
                            boxShadow: '0 2px 4px rgba(0,0,0,0.5)',
                          }}
                        >
                          Empty Mask
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {failedVolumes.length > 0 && (
              <div className="mt-2 rounded-lg border border-[#7f1d1d] bg-[#1a1010] p-3 text-xs text-red-200">
                <div className="mb-2 font-semibold text-red-300">
                  {failedCount} segmentation{failedCount === 1 ? ' failed' : 's failed'}
                </div>
                <div className="space-y-1 font-mono text-[10px] text-red-200/80">
                  {failedVolumes.map(volume => (
                    <div
                      key={volume.pairId || volume.seriesInstanceUid}
                      className="flex gap-2"
                    >
                      <span>{volume.seriesInstanceUid.slice(-8)}</span>
                      <span className="font-sans text-red-200/60">
                        {volume.reason || 'Segmentation failed.'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

      </div>

      {/* Save */}
      <div className="flex w-full shrink-0 items-center justify-center gap-6">
        {volumes.length > 0 && !isFinished && (
          <span className="flex items-center gap-2 rounded-full border border-[#5acce6]/30 bg-[#5acce6]/10 px-4 py-2.5 text-sm font-bold text-[#5acce6]">
            <Loader2
              size={16}
              className="animate-spin"
            />{' '}
            Processing segmentation pairs...
          </span>
        )}
        {volumes.length > 0 && isFinished && (
          <span className="flex items-center gap-2 rounded-full border border-[#6fe5b8]/30 bg-[#6fe5b8]/10 px-4 py-2.5 text-sm font-bold text-[#6fe5b8]">
            <Check
              size={16}
              strokeWidth={3}
            />{' '}
            Finished
          </span>
        )}
        <button
          onClick={handleSave}
          disabled={isLoading || volumes.length === 0 || isReviewLocked}
          className="flex items-center gap-2 rounded-full bg-[#5acce6] px-12 py-3 font-bold text-black shadow-[0_0_20px_rgba(90,204,230,0.2)] transition-colors hover:bg-[#4ab5cc] disabled:cursor-not-allowed disabled:bg-[#1f2d40] disabled:text-[#3c5d80]"
        >
          {saveState === 'saving' ? (
            <Loader2
              size={18}
              className="animate-spin"
            />
          ) : (
            <Save size={18} />
          )}
          {saveState === 'saving'
            ? 'Saving Review...'
            : saveState === 'submitted'
              ? 'Review Saved'
              : 'Save Review Results'}
        </button>
      </div>

      {editor && (
        <EmbeddedSegmentationModal
          viewerUrl={editor.viewerUrl}
          scanLabel={editor.volume.seriesInstanceUid}
          onCancel={() => setEditor(null)}
          onSubmitted={handleEditorSubmitted}
        />
      )}
    </div>
  );
}

export default function SegmentationView({ segmentationData }: ReviewContainerProps) {
  const [data, setData] = useState<{
    runId?: string;
    targetRoi: string;
    selectedModel: string;
    volumes: VolumeGif[];
    failedVolumes: FailedVolume[];
    progress?: SegmentationProgress;
    sessionId?: string;
    isFinished: boolean;
  }>({
    runId: undefined,
    targetRoi: '-',
    selectedModel: '-',
    volumes: [],
    failedVolumes: [],
    progress: undefined,
    sessionId: undefined,
    isFinished: false,
  });

  const [isLoading, setIsLoading] = useState(true);

  const snackbar = useSnackbar();

  useEffect(() => {
    if (!segmentationData) {
      // No data at all — reset to blank loading state
      setData({
        runId: undefined,
        targetRoi: '-',
        selectedModel: '-',
        volumes: [],
        failedVolumes: [],
        progress: undefined,
        sessionId: undefined,
        isFinished: false,
      });
      setIsLoading(true);
      return;
    }

    const hasEarlyInfo = segmentationData.targetRoi || segmentationData.selectedModel;
    const hasMaskData =
      segmentationData?.volumes?.length ||
      segmentationData?.failedVolumes?.length ||
      segmentationData?.isFinished;

    if (hasEarlyInfo || hasMaskData) {
      // Merge incoming data with existing state so early targetRoi/selectedModel
      // are preserved when subsequent mask updates arrive (and vice versa).
      setData(prev => ({
        runId: segmentationData.runId ?? prev.runId,
        targetRoi: segmentationData.targetRoi || prev.targetRoi || '-',
        selectedModel: segmentationData.selectedModel || prev.selectedModel || '-',
        volumes: segmentationData.volumes ?? prev.volumes ?? [],
        failedVolumes: segmentationData.failedVolumes ?? prev.failedVolumes ?? [],
        progress: segmentationData.progress ?? prev.progress,
        sessionId: segmentationData.sessionId ?? prev.sessionId,
        isFinished: segmentationData.isFinished ?? prev.isFinished ?? false,
      }));
      // Stop the loading spinner only once actual mask data (or finish) arrives
      if (hasMaskData) {
        setIsLoading(false);
      }
    } else {
      setIsLoading(true);
    }
  }, [segmentationData]);

  const handleSave = async (results: {
    accepted: string[];
    rejected: string[];
    modified: string[];
  }) => {
    if (!data.sessionId) {
      snackbar.show({
        title: 'Session Error',
        message: 'Session ID is missing. Unable to send review results.',
        type: 'error',
        duration: 5000,
      });
      return false;
    }
    console.log('the session id in front is', data.sessionId);

    try {
      await saveSegmentationReview(data.sessionId, results);
      snackbar.show({
        title: 'Reviews Saved',
        message: 'Done! Reviews saved — the AI module will now resume.',
        type: 'success',
        duration: 5000,
      });
      console.log('Review results sent successfully:', results);
      return true;
    } catch (error) {
      console.error('Failed to send review results:', error);
      snackbar.show({
        title: 'Save Failed',
        message: 'Failed to send review results to the backend. Please try again.',
        type: 'error',
        duration: 5000,
      });
      return false;
    }
  };

  const handleVolumeUpdated = useCallback((pairId: string, updates: Partial<VolumeGif>) => {
    setData(prev => ({
      ...prev,
      volumes: prev.volumes.map(volume =>
        (volume.pairId || volume.seriesInstanceUid) === pairId ? { ...volume, ...updates } : volume
      ),
    }));
  }, []);

  return (
    <ReviewView
      key={`${data.sessionId || 'no-session'}:${data.runId || 'legacy'}`}
      targetRoi={data.targetRoi}
      selectedModel={data.selectedModel}
      volumes={data.volumes}
      failedVolumes={data.failedVolumes}
      progress={data.progress}
      onSave={handleSave}
      isLoading={isLoading}
      isFinished={data.isFinished}
      sessionId={data.sessionId}
      runId={data.runId}
      onVolumeUpdated={handleVolumeUpdated}
    />
  );
}
