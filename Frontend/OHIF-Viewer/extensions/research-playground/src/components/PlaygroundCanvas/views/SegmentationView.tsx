import React, { useState, useCallback, useEffect } from 'react';
import { Check, X, CheckSquare, XSquare, Save, Loader2, Edit } from 'lucide-react';
import { VolumeGif, ReviewContainerProps, ReviewStatus } from './segmentation/types';
import { RightPanel } from './segmentation/Shared';
import { handleModifyAction, saveSegmentationReview } from './segmentation/utils';

// ─── Review View ──────────────────────────────────────────────────────────────

function ReviewView({
    targetRoi,
    selectedModel,
    volumes,
    onSave,
    isLoading,
    isFinished,
    sessionId,
}: {
    targetRoi: string;
    selectedModel: string;
    volumes: VolumeGif[];
    onSave: (results: { accepted: string[]; rejected: string[]; modified: string[] }) => void;
    isLoading: boolean;
    isFinished: boolean;
    sessionId?: string;
}) {
    const [reviewStatus, setReviewStatus] = useState<Record<string, ReviewStatus>>({});

    useEffect(() => {
        setReviewStatus(prev => {
            const next = { ...prev };
            let changed = false;
            volumes.forEach(v => {
                // Default to 'accepted' if this volume hasn't been seen yet.
                // This prevents resetting user choices when new GIFs progressively load in.
                if (!next[v.seriesInstanceUid]) {
                    next[v.seriesInstanceUid] = 'accepted';
                    changed = true;
                }
            });
            return changed ? next : prev;
        });
    }, [volumes]);

    const handleToggle = useCallback((uid: string, status: 'accepted' | 'rejected' | 'modified') =>
        setReviewStatus(prev => ({ ...prev, [uid]: status })), []);

    const handleAcceptAll = useCallback(() => {
        const s: Record<string, ReviewStatus> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'accepted'; });
        setReviewStatus(s);
    }, [volumes]);

    const handleRejectAll = useCallback(() => {
        const s: Record<string, ReviewStatus> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'rejected'; });
        setReviewStatus(s);
    }, [volumes]);

    const handleModify = async (uid: string) => {
        try {
            // Optimistically set status to modified (Yellow) so it's ready when the user returns
            handleToggle(uid, 'modified');

            const data = await handleModifyAction(uid);
            
            if (data.study_uid) {
                // Open Monai Label viewer in a new tab — this is a FRONTEND route, not a backend API path
                const params = new URLSearchParams({
                    StudyInstanceUIDs: data.study_uid,
                    session_id: sessionId || '',
                    series_uid: uid
                });
                window.open(`${window.location.origin}/monai-label?${params.toString()}`, '_blank');
            } else {
                console.error("study_uid not found in the response body", data);
                alert("Study UID missing from the server response.");
            }
        } catch (error) {
            console.error("Error retrieving study uid:", error);
            alert("Failed to connect to the backend to retrieve the Study UID.");
        }
    };

    const handleSave = () => {
        const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
        const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
        const modified = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'modified');
        onSave({ accepted, rejected, modified });
    };

    const acceptedCount = Object.values(reviewStatus).filter(s => s === 'accepted').length;
    const rejectedCount = Object.values(reviewStatus).filter(s => s === 'rejected').length;
    const modifiedCount = Object.values(reviewStatus).filter(s => s === 'modified').length;
    const pendingCount  = Object.values(reviewStatus).filter(s => s === 'pending').length;

    return (
        <div className="p-4 w-full h-full flex flex-col gap-4 bg-[#000000] overflow-hidden">
            <div className="flex gap-4 flex-1 min-h-0">

                {/* ── GIF Grid ── */}
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#1f2d40] p-4 flex flex-col min-h-0 relative overflow-hidden">

                    {/* Bulk actions + counters */}
                    <div className="flex items-center gap-3 mb-4 shrink-0 border-b border-[#1f2d40]/50 pb-4">
                        <button
                            onClick={handleRejectAll}
                            disabled={volumes.length === 0 || isLoading || !isFinished}
                            className="px-4 py-2 bg-[#1a1515] border border-red-900/50 text-red-400 hover:bg-red-900/20 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <XSquare size={15} /> Reject All
                        </button>
                        <button
                            onClick={handleAcceptAll}
                            disabled={volumes.length === 0 || isLoading || !isFinished}
                            className="px-4 py-2 bg-[#151f1a] border border-[#6fe5b8]/30 text-[#6fe5b8] hover:bg-[#6fe5b8]/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <CheckSquare size={15} /> Accept All
                        </button>

                        {volumes.length > 0 && (
                            <div className="ml-auto flex items-center gap-4 text-[10px]">
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
                        <div className="absolute inset-0 top-[73px] flex flex-col items-center justify-center z-20 bg-[#151a1f]/90 backdrop-blur-sm">
                            <div className="bg-[#0b1015] p-8 rounded-2xl border border-[#1f2d40] flex flex-col items-center text-center max-w-md shadow-2xl">
                                <div className="w-16 h-16 bg-[#3c5d80]/20 rounded-full flex items-center justify-center mb-4">
                                    <Loader2 size={32} className="text-[#5acce6] animate-spin" />
                                </div>
                                <h3 className="text-xl font-bold text-white mb-2">Awaiting Segmentation</h3>
                                <p className="text-[#566a84] text-sm leading-relaxed">
                                    Please wait until the segmentation agent finishes and sends the results.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* GIF Grid */}
                    <div className="flex-1 overflow-y-auto pr-1">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 pb-4">
                            {volumes.map(volume => {
                                const status     = reviewStatus[volume.seriesInstanceUid];
                                const isAccepted = status === 'accepted';
                                const isRejected = status === 'rejected';
                                const isModified = status === 'modified';

                                return (
                                    <div
                                        key={volume.seriesInstanceUid}
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
                                        <div style={{
                                            height: '36px', flexShrink: 0,
                                            background: 'rgba(0,0,0,0.8)',
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '0 8px',
                                            borderBottom: '1px solid rgba(31,45,64,0.5)',
                                        }}>
                                            <span style={{ fontSize: '10px', color: '#d1d5db', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '60%' }}>
                                                {volume.seriesInstanceUid.slice(-8)}
                                            </span>
                                            <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                                <button
                                                    onClick={() => handleModify(volume.seriesInstanceUid)}
                                                    disabled={!isFinished}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: isFinished ? 'pointer' : 'not-allowed', background: isModified ? '#eab308' : 'transparent', color: isModified ? '#000' : '#9ca3af', opacity: isFinished ? 1 : 0.5 }}
                                                    title="Modify Interactively"
                                                ><Edit size={14} /></button>
                                                <button
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'rejected')}
                                                    disabled={!isFinished}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: isFinished ? 'pointer' : 'not-allowed', background: isRejected ? '#ef4444' : 'transparent', color: isRejected ? '#fff' : '#9ca3af', opacity: isFinished ? 1 : 0.5 }}
                                                ><X size={14} /></button>
                                                <button
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'accepted')}
                                                    disabled={!isFinished}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: isFinished ? 'pointer' : 'not-allowed', background: isAccepted ? '#6fe5b8' : 'transparent', color: isAccepted ? '#000' : '#9ca3af', opacity: isFinished ? 1 : 0.5 }}
                                                ><Check size={14} strokeWidth={3} /></button>
                                            </div>
                                        </div>

                                        {/* GIF */}
                                        <div style={{ flex: 1, background: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                                            <img
                                                src={volume.gifUrl}
                                                alt={volume.seriesInstanceUid.slice(-8)}
                                                style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
                                            />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* ── Right Panel ── */}
                <RightPanel
                    targetRoi={targetRoi}
                    selectedModel={selectedModel}
                />
            </div>

            {/* Save */}
            <div className="w-full flex justify-center items-center gap-6 shrink-0">
                {volumes.length > 0 && !isFinished && (
                    <span className="flex items-center gap-2 bg-[#5acce6]/10 border border-[#5acce6]/30 px-4 py-2.5 rounded-full text-[#5acce6] font-bold text-sm">
                        <Loader2 size={16} className="animate-spin" /> Loading GIFs...
                    </span>
                )}
                {volumes.length > 0 && isFinished && (
                    <span className="flex items-center gap-2 bg-[#6fe5b8]/10 border border-[#6fe5b8]/30 px-4 py-2.5 rounded-full text-[#6fe5b8] font-bold text-sm">
                        <Check size={16} strokeWidth={3} /> Finished
                    </span>
                )}
                <button
                    onClick={handleSave}
                    disabled={isLoading || volumes.length === 0 || !isFinished}
                    className="px-12 py-3 bg-[#5acce6] hover:bg-[#4ab5cc] disabled:bg-[#1f2d40] disabled:text-[#3c5d80] disabled:cursor-not-allowed text-black font-bold rounded-full flex items-center gap-2 transition-colors shadow-[0_0_20px_rgba(90,204,230,0.2)]"
                >
                    <Save size={18} /> Save Review Results
                </button>
            </div>
        </div>
    );
}

// ─── Container (default export) ───────────────────────────────────────────────

export default function SegmentationView({ segmentationData }: ReviewContainerProps) {
    const [data, setData] = useState<{
        targetRoi: string;
        selectedModel: string;
        volumes: VolumeGif[];
        sessionId?: string;
        isFinished: boolean;
    }>({ targetRoi: '-', selectedModel: '-', volumes: [], sessionId: undefined, isFinished: false });

    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        if (segmentationData?.volumes?.length) {
            setData({
                targetRoi:     segmentationData.targetRoi     || '-',
                selectedModel: segmentationData.selectedModel || '-',
                volumes:       segmentationData.volumes,
                sessionId:     segmentationData.sessionId,
                isFinished:    segmentationData.isFinished ?? false,
            });
            setIsLoading(false);
        } else {
            setIsLoading(true);
        }
    }, [segmentationData]);

    const handleSave = async (results: { accepted: string[]; rejected: string[]; modified: string[] }) => {
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

    return (
        <ReviewView
            targetRoi={data.targetRoi}
            selectedModel={data.selectedModel}
            volumes={data.volumes}
            onSave={handleSave}
            isLoading={isLoading}
            isFinished={data.isFinished}
            sessionId={data.sessionId}
        />
    );
}
