import React, { useEffect, useState } from 'react';
import { Target, Cpu, Percent, Check, X, CheckSquare, XSquare, Save, Loader2 } from 'lucide-react';
import websocketService from '../../../utils/WebsocketService';

export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface ReviewViewProps {
    targetRoi: string;
    selectedModel: string;
    diceScore: number | null;
    volumes: VolumeGif[];
    onSave: (results: { accepted: string[]; rejected: string[] }) => void;
    isLoading: boolean;
}

function ReviewView({ targetRoi, selectedModel, diceScore, volumes = [], onSave, isLoading }: ReviewViewProps) {
    const [reviewStatus, setReviewStatus] = useState<Record<string, 'accepted' | 'rejected' | 'pending'>>({});

    useEffect(() => {
        const initialStatus: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(vol => { initialStatus[vol.seriesInstanceUid] = 'pending'; });
        setReviewStatus(initialStatus);
    }, [volumes]);

    const handleToggle = (uid: string, status: 'accepted' | 'rejected') => {
        setReviewStatus(prev => ({ ...prev, [uid]: status }));
    };

    const handleAcceptAll = () => {
        const s: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'accepted'; });
        setReviewStatus(s);
    };

    const handleRejectAll = () => {
        const s: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { s[v.seriesInstanceUid] = 'rejected'; });
        setReviewStatus(s);
    };

    const handleSave = () => {
        const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
        const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
        onSave({ accepted, rejected });
    };

    return (
        <div className="p-6 w-full h-full flex flex-col gap-6 bg-[#000000] overflow-hidden">
            <div className="flex gap-6 flex-1 min-h-0">

                {/* LEFT: Review Grid */}
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col min-h-0 relative overflow-hidden">

                    {/* Bulk actions */}
                    <div className="flex justify-start gap-3 mb-4 shrink-0 border-b border-[#3c5d80]/50 pb-4">
                        <button
                            onClick={handleRejectAll}
                            disabled={volumes.length === 0 || isLoading}
                            className="px-4 py-2 bg-[#1a1515] border border-red-900/50 text-red-400 hover:bg-red-900/20 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <XSquare size={16} /> Reject All
                        </button>
                        <button
                            onClick={handleAcceptAll}
                            disabled={volumes.length === 0 || isLoading}
                            className="px-4 py-2 bg-[#151f1a] border border-[#6fe5b8]/30 text-[#6fe5b8] hover:bg-[#6fe5b8]/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <CheckSquare size={16} /> Accept All
                        </button>
                    </div>

                    {/* Loading overlay */}
                    {isLoading && volumes.length === 0 && (
                        <div className="absolute inset-0 top-[70px] flex flex-col items-center justify-center z-20 bg-[#151a1f]/90 backdrop-blur-sm">
                            <div className="bg-[#0b1015] p-8 rounded-2xl border border-[#3c5d80] flex flex-col items-center text-center max-w-md shadow-2xl">
                                <div className="w-16 h-16 bg-[#3c5d80]/20 rounded-full flex items-center justify-center mb-4">
                                    <Loader2 size={32} className="text-[#5acce6] animate-spin" />
                                </div>
                                <h3 className="text-xl font-bold text-white mb-2">Awaiting Segmentation</h3>
                                <p className="text-gray-400 text-sm">
                                    Please wait until the segmentation agent finishes and sends the results.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* GIF Grid */}
                    <div className="flex-1 overflow-y-auto pr-2">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 pb-4">
                            {volumes.map((volume) => {
                                const status = reviewStatus[volume.seriesInstanceUid];
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
                                            border: `2px solid ${isAccepted ? '#6fe5b8' : isRejected ? '#ef4444' : 'rgba(60,93,128,0.5)'}`,
                                            background: '#0b1015',
                                            transition: 'border-color 0.2s',
                                        }}
                                    >
                                        {/* Header — fixed 36px */}
                                        <div style={{ height: '36px', flexShrink: 0, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 8px', borderBottom: '1px solid rgba(60,93,128,0.3)' }}>
                                            <span style={{ fontSize: '10px', color: '#d1d5db', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '60%' }}>
                                                {volume.seriesInstanceUid.slice(-8)}
                                            </span>
                                            <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                                <button
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'rejected')}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: isRejected ? '#ef4444' : 'transparent', color: isRejected ? '#fff' : '#9ca3af' }}
                                                >
                                                    <X size={14} />
                                                </button>
                                                <button
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'accepted')}
                                                    style={{ padding: '3px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: isAccepted ? '#6fe5b8' : 'transparent', color: isAccepted ? '#000' : '#9ca3af' }}
                                                >
                                                    <Check size={14} strokeWidth={3} />
                                                </button>
                                            </div>
                                        </div>

                                        {/* GIF — remaining 184px, all inline so nothing fights it */}
                                        <div style={{ height: '184px', background: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
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

                {/* RIGHT: Meta panel */}
                <div className="w-64 flex flex-col gap-4 shrink-0">
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col gap-6">
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Target size={12} /> Target ROI
                            </h3>
                            <div className="text-lg font-bold text-[#5acce6]">{targetRoi}</div>
                        </div>
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Cpu size={12} /> Selected Model
                            </h3>
                            <div className="text-md font-bold text-white">{selectedModel}</div>
                        </div>
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Percent size={12} /> Benchmarking Dice Score
                            </h3>
                            <div className="text-xl font-mono font-bold text-[#6fe5b8]">
                                {diceScore !== null ? diceScore.toFixed(3) : '-'}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Save button */}
            <div className="w-full flex justify-center shrink-0 pt-2">
                <button
                    onClick={handleSave}
                    disabled={isLoading || volumes.length === 0}
                    className="px-12 py-3 bg-[#5acce6] hover:bg-[#4ab5cc] disabled:bg-[#3c5d80] disabled:text-gray-400 disabled:cursor-not-allowed text-black font-bold rounded-full flex items-center gap-2 transition-colors shadow-[0_0_15px_rgba(90,204,230,0.3)]"
                >
                    <Save size={18} /> Save Review Results
                </button>
            </div>
        </div>
    );
}

// --- Container ---
interface FeaturesViewContainerProps {
    featuresData?: any;
    importanceData?: any;
}

export default function ReviewContainer({ featuresData, importanceData }: FeaturesViewContainerProps) {
    const [data, setData] = useState<{
        targetRoi: string;
        selectedModel: string;
        diceScore: number | null;
        volumes: VolumeGif[];
    }>({
        targetRoi: "-",
        selectedModel: "-",
        diceScore: null,
        volumes: []
    });

    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const source = importanceData?.volumes?.length > 0 ? importanceData
                     : featuresData?.volumes?.length > 0   ? featuresData
                     : null;

        if (source) {
            setData({
                targetRoi:     source.targetRoi     || "-",
                selectedModel: source.selectedModel || "-",
                diceScore:     source.diceScore     ?? null,
                volumes:       source.volumes,
            });
            setIsLoading(false);
        } else {
            setIsLoading(true);
        }
    }, [featuresData, importanceData]);

    const handleSaveResults = (results: { accepted: string[]; rejected: string[] }) => {
        try {
            websocketService.send(JSON.stringify({
                accepted: results.accepted,
                rejected: results.rejected,
            }));
            alert("Review saved! The AI module will now resume.");
        } catch (error) {
            console.error("Failed to send review results:", error);
            alert("Failed to connect to the AI agent. Please try again.");
        }
    };

    return (
        <ReviewView
            targetRoi={data.targetRoi}
            selectedModel={data.selectedModel}
            diceScore={data.diceScore}
            volumes={data.volumes}
            onSave={handleSaveResults}
            isLoading={isLoading}
        />
    );
}