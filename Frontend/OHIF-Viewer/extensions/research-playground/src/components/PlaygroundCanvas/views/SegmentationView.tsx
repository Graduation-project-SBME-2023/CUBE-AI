import React, { useState, useCallback, useEffect } from 'react';
import {
    Target, Cpu, Percent, Check, X, CheckSquare, XSquare, Save, Loader2,
    ChevronDown, ChevronUp,
} from 'lucide-react';
import websocketService from '../../../utils/WebsocketService';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface SegmentationData {
    volumes?: VolumeGif[];
    targetRoi?: string;
    selectedModel?: string;
    diceScore?: number | null;
}

interface ReviewContainerProps {
    segmentationData?: SegmentationData | null;
}

// ─── Collapsible Section ──────────────────────────────────────────────────────

function CollapsibleSection({
    title,
    icon,
    defaultOpen = true,
    children,
    accent = '#5acce6',
}: {
    title: string;
    icon: React.ReactNode;
    defaultOpen?: boolean;
    children: React.ReactNode;
    accent?: string;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="bg-[#151a1f] rounded-xl border border-[#1f2d40] overflow-hidden">
            <button
                onClick={() => setOpen(o => !o)}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.025] transition-colors"
            >
                <span
                    className="flex items-center gap-2 text-[10px] uppercase font-semibold tracking-wider"
                    style={{ color: accent }}
                >
                    {icon} {title}
                </span>
                {open
                    ? <ChevronUp size={13} className="text-[#566a84]" />
                    : <ChevronDown size={13} className="text-[#566a84]" />}
            </button>
            {open && <div className="px-4 pb-4">{children}</div>}
        </div>
    );
}

// ─── Right Panel ──────────────────────────────────────────────────────────────

function RightPanel({
    targetRoi,
    selectedModel,
    diceScore,
}: {
    targetRoi: string;
    selectedModel: string;
    diceScore: number | null;
}) {
    return (
        <div className="w-64 flex flex-col gap-3 shrink-0 overflow-y-auto pr-0.5">
            <CollapsibleSection title="Segmentation Info" icon={<Target size={11} />} accent="#5acce6">
                <div className="flex flex-col gap-4 pt-1">
                    <div>
                        <p className="text-[#566a84] text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Target size={11} /> Target ROI
                        </p>
                        <div className="text-base font-bold text-[#5acce6]">{targetRoi}</div>
                    </div>
                    <div>
                        <p className="text-[#566a84] text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Cpu size={11} /> Selected Model
                        </p>
                        <div className="text-sm font-bold text-white">{selectedModel}</div>
                    </div>
                    <div>
                        <p className="text-[#566a84] text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Percent size={11} /> Benchmarking Dice Score
                        </p>
                        <div className="text-xl font-mono font-bold text-[#6fe5b8]">
                            {diceScore !== null ? diceScore.toFixed(3) : '—'}
                        </div>
                    </div>
                </div>
            </CollapsibleSection>
        </div>
    );
}

// ─── Review View ──────────────────────────────────────────────────────────────

function ReviewView({
    targetRoi,
    selectedModel,
    diceScore,
    volumes,
    onSave,
    isLoading,
}: {
    targetRoi: string;
    selectedModel: string;
    diceScore: number | null;
    volumes: VolumeGif[];
    onSave: (results: { accepted: string[]; rejected: string[] }) => void;
    isLoading: boolean;
}) {
    const [reviewStatus, setReviewStatus] = useState<Record<string, 'accepted' | 'rejected' | 'pending'>>({});

    useEffect(() => {
        const initial: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { initial[v.seriesInstanceUid] = 'pending'; });
        setReviewStatus(initial);
    }, [volumes]);

    const handleToggle = useCallback((uid: string, status: 'accepted' | 'rejected') =>
        setReviewStatus(prev => ({ ...prev, [uid]: status })), []);

    const handleAcceptAll = useCallback(() => {
        const s: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'accepted'; });
        setReviewStatus(s);
    }, [volumes]);

    const handleRejectAll = useCallback(() => {
        const s: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'rejected'; });
        setReviewStatus(s);
    }, [volumes]);

    const handleSave = () => {
        const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
        const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
        onSave({ accepted, rejected });
    };

    const acceptedCount = Object.values(reviewStatus).filter(s => s === 'accepted').length;
    const rejectedCount = Object.values(reviewStatus).filter(s => s === 'rejected').length;
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
                            disabled={volumes.length === 0 || isLoading}
                            className="px-4 py-2 bg-[#1a1515] border border-red-900/50 text-red-400 hover:bg-red-900/20 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <XSquare size={15} /> Reject All
                        </button>
                        <button
                            onClick={handleAcceptAll}
                            disabled={volumes.length === 0 || isLoading}
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

                                return (
                                    <div
                                        key={volume.seriesInstanceUid}
                                        style={{
                                            height: '220px',
                                            display: 'flex',
                                            flexDirection: 'column',
                                            borderRadius: '12px',
                                            overflow: 'hidden',
                                            border: `2px solid ${isAccepted ? '#6fe5b8' : isRejected ? '#ef4444' : 'rgba(31,45,64,0.8)'}`,
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
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'rejected')}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: isRejected ? '#ef4444' : 'transparent', color: isRejected ? '#fff' : '#9ca3af' }}
                                                ><X size={14} /></button>
                                                <button
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'accepted')}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: isAccepted ? '#6fe5b8' : 'transparent', color: isAccepted ? '#000' : '#9ca3af' }}
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
                    diceScore={diceScore}
                />
            </div>

            {/* Save */}
            <div className="w-full flex justify-center shrink-0">
                <button
                    onClick={handleSave}
                    disabled={isLoading || volumes.length === 0}
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
        diceScore: number | null;
        volumes: VolumeGif[];
    }>({ targetRoi: '-', selectedModel: '-', diceScore: null, volumes: [] });

    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        if (segmentationData?.volumes?.length) {
            setData({
                targetRoi:     segmentationData.targetRoi     || '-',
                selectedModel: segmentationData.selectedModel || '-',
                diceScore:     segmentationData.diceScore     ?? null,
                volumes:       segmentationData.volumes,
            });
            setIsLoading(false);
        } else {
            setIsLoading(true);
        }
    }, [segmentationData]);

    const handleSave = (results: { accepted: string[]; rejected: string[] }) => {
        try {
            websocketService.send(JSON.stringify({ accepted: results.accepted, rejected: results.rejected }));
            alert('Review saved! The AI module will now resume.');
        } catch (error) {
            console.error('Failed to send review results:', error);
            alert('Failed to connect to the AI agent. Please try again.');
        }
    };

    return (
        <ReviewView
            targetRoi={data.targetRoi}
            selectedModel={data.selectedModel}
            diceScore={data.diceScore}
            volumes={data.volumes}
            onSave={handleSave}
            isLoading={isLoading}
        />
    );
}