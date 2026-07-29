import React, { useState, useCallback, useEffect } from 'react';
import { Check, X, CheckSquare, XSquare, Save, Loader2, Edit } from 'lucide-react';
import {
  FailedVolume,
  SegmentationProgress,
  VolumeGif,
  ReviewContainerProps,
  ReviewStatus,
} from './segmentation/types';
import { RightPanel } from './segmentation/Shared';
import EmbeddedSegmentationModal, {
  SegmentationSubmitPayload,
} from './segmentation/EmbeddedSegmentationModal';
import { addCacheBuster, handleModifyAction, saveSegmentationReview } from './segmentation/utils';

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
  onVolumeUpdated,
}: {
  targetRoi: string;
  selectedModel: string;
  volumes: VolumeGif[];
  failedVolumes: FailedVolume[];
  progress?: SegmentationProgress;
  onSave: (results: { accepted: string[]; rejected: string[]; modified: string[] }) => void;
  isLoading: boolean;
  isFinished: boolean;
  sessionId?: string;
  onVolumeUpdated: (pairId: string, updates: Partial<VolumeGif>) => void;
}) {
  const [reviewStatus, setReviewStatus] = useState<Record<string, ReviewStatus>>({});
  const [openingPairId, setOpeningPairId] = useState<string | null>(null);
  const [previewRefresh, setPreviewRefresh] = useState<Record<string, 'loading' | 'error'>>({});
  const [editor, setEditor] = useState<{
    volume: VolumeGif;
    reviewKey: string;
    viewerUrl: string;
  } | null>(null);

  useEffect(() => {
    setReviewStatus(prev => {
      const next = { ...prev };
      let changed = false;
      volumes.forEach(v => {
        const reviewKey = v.pairId || v.seriesInstanceUid;
        // Default to 'accepted' if this volume hasn't been seen yet.
        // This prevents resetting user choices when new GIFs progressively load in.
        if (!next[reviewKey]) {
          next[reviewKey] = 'accepted';
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [volumes]);

  const handleToggle = useCallback(
    (uid: string, status: 'accepted' | 'rejected' | 'modified') =>
      setReviewStatus(prev => ({ ...prev, [uid]: status })),
    []
  );

  const handleAcceptAll = useCallback(() => {
    const s: Record<string, ReviewStatus> = {};
    volumes.forEach(v => {
      s[v.pairId || v.seriesInstanceUid] = 'accepted';
    });
    setReviewStatus(s);
  }, [volumes]);

  const handleRejectAll = useCallback(() => {
    const s: Record<string, ReviewStatus> = {};
    volumes.forEach(v => {
      s[v.pairId || v.seriesInstanceUid] = 'rejected';
    });
    setReviewStatus(s);
  }, [volumes]);

  const handleModify = async (volume: VolumeGif) => {
    const uid = volume.seriesInstanceUid;
    const reviewKey = volume.pairId || uid;
    if (!sessionId) {
      alert('Session ID is missing. Unable to open the segmentation editor.');
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
        alert('Study UID missing from the server response.');
      }
    } catch (error) {
      console.error('Error retrieving study uid:', error);
      alert('Failed to connect to the backend to retrieve the Study UID.');
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

  const handleSave = () => {
    const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
    const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
    const modified = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'modified');
    onSave({ accepted, rejected, modified });
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
      <div className="flex min-h-0 flex-1 gap-4">
        {/* ── GIF Grid ── */}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[#1f2d40] bg-[#151a1f] p-4">
          {/* Bulk actions + counters */}
          <div className="mb-4 flex shrink-0 items-center gap-3 border-b border-[#1f2d40]/50 pb-4">
            <button
              onClick={handleRejectAll}
              disabled={volumes.length === 0 || isLoading || !isFinished}
              className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-[#1a1515] px-4 py-2 text-sm text-red-400 transition-colors hover:bg-red-900/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <XSquare size={15} /> Reject All
            </button>
            <button
              onClick={handleAcceptAll}
              disabled={volumes.length === 0 || isLoading || !isFinished}
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
                <h3 className="mb-2 text-xl font-bold text-white">Awaiting Segmentation</h3>
                <p className="text-sm leading-relaxed text-[#566a84]">
                  Please wait until the segmentation agent finishes and sends the results.
                </p>
              </div>
            </div>
          )}

          {/* GIF Grid */}
          <div className="flex-1 overflow-y-auto pr-1">
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
                          disabled={!isFinished || openingPairId === reviewKey}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor:
                              isFinished && openingPairId !== reviewKey ? 'pointer' : 'not-allowed',
                            background: isModified ? '#eab308' : 'transparent',
                            color: isModified ? '#000' : '#9ca3af',
                            opacity: isFinished ? 1 : 0.5,
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
                          disabled={!isFinished}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor: isFinished ? 'pointer' : 'not-allowed',
                            background: isRejected ? '#ef4444' : 'transparent',
                            color: isRejected ? '#fff' : '#9ca3af',
                            opacity: isFinished ? 1 : 0.5,
                          }}
                        >
                          <X size={14} />
                        </button>
                        <button
                          onClick={() => handleToggle(reviewKey, 'accepted')}
                          disabled={!isFinished}
                          style={{
                            padding: '3px',
                            borderRadius: '4px',
                            border: 'none',
                            cursor: isFinished ? 'pointer' : 'not-allowed',
                            background: isAccepted ? '#6fe5b8' : 'transparent',
                            color: isAccepted ? '#000' : '#9ca3af',
                            opacity: isFinished ? 1 : 0.5,
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
                  {failedCount} volume
                  {failedCount === 1 ? ' mask is empty or missing' : ' masks are empty or missing'}
                </div>
                <div className="space-y-1 font-mono text-[10px] text-red-200/80">
                  {failedVolumes.map(volume => (
                    <div
                      key={volume.pairId || volume.seriesInstanceUid}
                      className="flex gap-2"
                    >
                      <span>{volume.seriesInstanceUid.slice(-8)}</span>
                      <span className="font-sans text-red-200/60">
                        {volume.reason || 'Volume mask is empty.'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Right Panel ── */}
        <RightPanel
          targetRoi={targetRoi}
          selectedModel={selectedModel}
        />
      </div>

      {/* Save */}
      <div className="flex w-full shrink-0 items-center justify-center gap-6">
        {volumes.length > 0 && !isFinished && (
          <span className="flex items-center gap-2 rounded-full border border-[#5acce6]/30 bg-[#5acce6]/10 px-4 py-2.5 text-sm font-bold text-[#5acce6]">
            <Loader2
              size={16}
              className="animate-spin"
            />{' '}
            Loading GIFs...
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
          disabled={isLoading || volumes.length === 0 || !isFinished}
          className="flex items-center gap-2 rounded-full bg-[#5acce6] px-12 py-3 font-bold text-black shadow-[0_0_20px_rgba(90,204,230,0.2)] transition-colors hover:bg-[#4ab5cc] disabled:cursor-not-allowed disabled:bg-[#1f2d40] disabled:text-[#3c5d80]"
        >
          <Save size={18} /> Save Review Results
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
    targetRoi: string;
    selectedModel: string;
    volumes: VolumeGif[];
    failedVolumes: FailedVolume[];
    progress?: SegmentationProgress;
    sessionId?: string;
    isFinished: boolean;
  }>({
    targetRoi: '-',
    selectedModel: '-',
    volumes: [],
    failedVolumes: [],
    progress: undefined,
    sessionId: undefined,
    isFinished: false,
  });

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (
      segmentationData?.volumes?.length ||
      segmentationData?.failedVolumes?.length ||
      segmentationData?.isFinished
    ) {
      setData({
        targetRoi: segmentationData.targetRoi || '-',
        selectedModel: segmentationData.selectedModel || '-',
        volumes: segmentationData.volumes || [],
        failedVolumes: segmentationData.failedVolumes || [],
        progress: segmentationData.progress,
        sessionId: segmentationData.sessionId,
        isFinished: segmentationData.isFinished ?? false,
      });
      setIsLoading(false);
    } else if (!segmentationData) {
      setData({
        targetRoi: '-',
        selectedModel: '-',
        volumes: [],
        failedVolumes: [],
        progress: undefined,
        sessionId: undefined,
        isFinished: false,
      });
      setIsLoading(true);
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
      alert('Session ID is missing. Unable to send review results.');
      return;
    }
    console.log('the session id in front is', data.sessionId);

    try {
      await saveSegmentationReview(data.sessionId, results);
      alert('Review saved! The AI module will now resume.');
      console.log('Review results sent successfully:', results);
    } catch (error) {
      console.error('Failed to send review results:', error);
      alert('Failed to send review results to the backend. Please try again.');
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
      targetRoi={data.targetRoi}
      selectedModel={data.selectedModel}
      volumes={data.volumes}
      failedVolumes={data.failedVolumes}
      progress={data.progress}
      onSave={handleSave}
      isLoading={isLoading}
      isFinished={data.isFinished}
      sessionId={data.sessionId}
      onVolumeUpdated={handleVolumeUpdated}
    />
  );
}
