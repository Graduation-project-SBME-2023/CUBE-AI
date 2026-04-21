import React, { useEffect, useState } from 'react';
import {
    Target, Cpu, Percent, Check, X, CheckSquare, XSquare, Save, Loader2,
    Scan, BrainCircuit, List, CheckCircle, FileSpreadsheet, Clock, BarChart3,
    ChevronDown, ChevronUp
} from 'lucide-react';
import websocketService from '../../../utils/WebsocketService';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface FeaturesData {
    status: string;
    csv_paths?: Record<string, string>;
    num_subjects?: number;
    num_features?: Record<string, number>;
    mask_labels?: number[];
    processing_time_seconds?: number;
}

export interface FeatureImportanceData {
    status: string;
    method?: string;
    feature_count?: number;
    result_files?: Record<string, string>;
    volumes?: VolumeGif[];
    targetRoi?: string;
    selectedModel?: string;
    diceScore?: number | null;
}

export interface ReviewViewProps {
    targetRoi: string;
    selectedModel: string;
    diceScore: number | null;
    volumes: VolumeGif[];
    onSave: (results: { accepted: string[]; rejected: string[] }) => void;
    isLoading: boolean;
    featuresData?: FeaturesData | null;
    importanceData?: FeatureImportanceData | null;
}

// ─── Collapsible Section ──────────────────────────────────────────────────────

function CollapsibleSection({
    title,
    icon,
    defaultOpen = true,
    children,
    accentColor = '#5acce6',
}: {
    title: string;
    icon: React.ReactNode;
    defaultOpen?: boolean;
    children: React.ReactNode;
    accentColor?: string;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] overflow-hidden">
            <button
                onClick={() => setOpen(o => !o)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/5 transition-colors"
            >
                <span className="flex items-center gap-2 text-[10px] uppercase font-semibold tracking-wider" style={{ color: accentColor }}>
                    {icon} {title}
                </span>
                {open ? <ChevronUp size={14} className="text-gray-500" /> : <ChevronDown size={14} className="text-gray-500" />}
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
    featuresData,
    importanceData,
}: Pick<ReviewViewProps, 'targetRoi' | 'selectedModel' | 'diceScore' | 'featuresData' | 'importanceData'>) {
    const hasFeatures = featuresData && featuresData.status === 'success';
    const hasImportance = importanceData && importanceData.status === 'success';

    const featureEntries = Object.entries(featuresData?.num_features || {});
    const totalFeatures = featureEntries.reduce((sum, [, c]) => sum + c, 0);
    const csvEntries = Object.entries(featuresData?.csv_paths || {});

    return (
        <div className="w-72 flex flex-col gap-3 shrink-0 overflow-y-auto pr-0.5">

            {/* Segmentation Meta */}
            <CollapsibleSection title="Segmentation Info" icon={<Target size={12} />} accentColor="#5acce6">
                <div className="flex flex-col gap-4 pt-1">
                    <div>
                        <p className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Target size={11} /> Target ROI
                        </p>
                        <div className="text-base font-bold text-[#5acce6]">{targetRoi}</div>
                    </div>
                    <div>
                        <p className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Cpu size={11} /> Selected Model
                        </p>
                        <div className="text-sm font-bold text-white">{selectedModel}</div>
                    </div>
                    <div>
                        <p className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Percent size={11} /> Benchmarking Dice Score
                        </p>
                        <div className="text-xl font-mono font-bold text-[#6fe5b8]">
                            {diceScore !== null ? diceScore.toFixed(3) : '—'}
                        </div>
                    </div>
                </div>
            </CollapsibleSection>

            {/* Feature Extraction Summary */}
            {hasFeatures ? (
                <CollapsibleSection title="Feature Extraction" icon={<CheckCircle size={12} />} accentColor="#6fe5b8">
                    <div className="flex flex-col gap-3 pt-1">
                        <div className="grid grid-cols-2 gap-2">
                            <div className="bg-[#0b1015] rounded-lg p-3 border border-[#3c5d80]/40">
                                <div className="text-[10px] text-gray-500 uppercase mb-1">Subjects</div>
                                <div className="text-lg font-semibold text-gray-100">{featuresData.num_subjects ?? '—'}</div>
                            </div>
                            <div className="bg-[#0b1015] rounded-lg p-3 border border-[#3c5d80]/40">
                                <div className="text-[10px] text-gray-500 uppercase mb-1">Features</div>
                                <div className="text-lg font-semibold text-gray-100">{totalFeatures}</div>
                            </div>
                        </div>

                        {featuresData.mask_labels && featuresData.mask_labels.length > 0 && (
                            <div>
                                <div className="text-[10px] text-gray-500 uppercase mb-1">Labels</div>
                                <div className="flex flex-wrap gap-1">
                                    {featuresData.mask_labels.map(l => (
                                        <span key={l} className="text-[10px] bg-[#3c5d80]/30 text-[#5acce6] px-2 py-0.5 rounded font-mono">{l}</span>
                                    ))}
                                </div>
                            </div>
                        )}

                        {featuresData.processing_time_seconds != null && (
                            <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
                                <Clock size={11} /> {featuresData.processing_time_seconds.toFixed(1)}s processing time
                            </div>
                        )}

                        {featureEntries.length > 0 && (
                            <div>
                                <div className="text-[10px] text-gray-500 uppercase mb-2">By Label</div>
                                <div className="space-y-1">
                                    {featureEntries.map(([label, count]) => (
                                        <div key={label} className="flex justify-between items-center text-xs py-0.5">
                                            <span className="text-gray-400">Label {label}</span>
                                            <span className="text-gray-100 font-mono">{count}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                </CollapsibleSection>
            ) : (
                <CollapsibleSection title="Feature Extraction" icon={<Scan size={12} />} accentColor="#3c5d80">
                    <div className="flex flex-col items-center py-4 gap-3 text-center">
                        <div className="relative">
                            <Scan size={36} className="text-gray-600" />
                            <BrainCircuit size={18} className="text-[#5acce6] absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
                        </div>
                        <p className="text-[11px] text-gray-500 leading-relaxed">
                            Feature extraction not yet complete. Results will appear here once ready.
                        </p>
                        <button className="px-4 py-1.5 border border-[#3c5d80] text-[#5acce6] rounded-lg text-[11px] hover:bg-[#3c5d80]/20 transition-colors flex items-center gap-1.5">
                            <List size={11} /> Configure Radiomics
                        </button>
                    </div>
                </CollapsibleSection>
            )}

            {/* Feature Importance */}
            {hasImportance && (
                <CollapsibleSection title="Importance Analysis" icon={<BarChart3 size={12} />} accentColor="#a78bfa">
                    <div className="flex flex-col gap-2 pt-1 text-xs">
                        <div className="flex justify-between">
                            <span className="text-gray-500">Method</span>
                            <span className="text-gray-100">{importanceData.method}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-gray-500">Features Analyzed</span>
                            <span className="text-gray-100">{importanceData.feature_count}</span>
                        </div>
                        {importanceData.result_files && (
                            <div className="text-[11px] text-gray-600 mt-1">
                                {Object.keys(importanceData.result_files).length} result files generated
                            </div>
                        )}
                    </div>
                </CollapsibleSection>
            )}

            {/* CSV Outputs */}
            {hasFeatures && csvEntries.length > 0 && (
                <CollapsibleSection title="Feature Files" icon={<FileSpreadsheet size={12} />} defaultOpen={false} accentColor="#f59e0b">
                    <div className="flex flex-col gap-2 pt-1">
                        {csvEntries.map(([label, path]) => (
                            <div key={label} className="bg-[#0b1015] rounded-lg border border-[#3c5d80]/40 p-3 hover:border-[#5acce6]/40 transition-colors">
                                <div className="flex items-center justify-between mb-1">
                                    <span className="text-[11px] font-medium text-gray-200">{label}</span>
                                    <span className="text-[9px] bg-[#6fe5b8]/20 text-[#6fe5b8] font-semibold px-1.5 py-0.5 rounded uppercase">Ready</span>
                                </div>
                                <div className="text-[10px] text-gray-600 font-mono truncate">{path}</div>
                            </div>
                        ))}
                    </div>
                </CollapsibleSection>
            )}
        </div>
    );
}

// ─── Review View (Main) ───────────────────────────────────────────────────────

function ReviewView({
    targetRoi,
    selectedModel,
    diceScore,
    volumes = [],
    onSave,
    isLoading,
    featuresData,
    importanceData,
}: ReviewViewProps) {
    const [reviewStatus, setReviewStatus] = useState<Record<string, 'accepted' | 'rejected' | 'pending'>>({});

    useEffect(() => {
        const initial: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { initial[v.seriesInstanceUid] = 'pending'; });
        setReviewStatus(initial);
    }, [volumes]);

    const handleToggle = (uid: string, status: 'accepted' | 'rejected') =>
        setReviewStatus(prev => ({ ...prev, [uid]: status }));

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

                {/* ── MAIN: Segmentation Review Grid ── */}
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
                                        {/* Card Header */}
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

                                        {/* GIF */}
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

                {/* ── RIGHT: Meta + Features Panel ── */}
                <RightPanel
                    targetRoi={targetRoi}
                    selectedModel={selectedModel}
                    diceScore={diceScore}
                    featuresData={featuresData}
                    importanceData={importanceData}
                />
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

// ─── Container ────────────────────────────────────────────────────────────────

interface ReviewContainerProps {
    featuresData?: FeaturesData | null;
    importanceData?: FeatureImportanceData | null;
}

export default function ReviewContainer({ featuresData, importanceData }: ReviewContainerProps) {
    const [data, setData] = useState<{
        targetRoi: string;
        selectedModel: string;
        diceScore: number | null;
        volumes: VolumeGif[];
    }>({
        targetRoi: '-',
        selectedModel: '-',
        diceScore: null,
        volumes: [],
    });

    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const source = importanceData?.volumes?.length ? importanceData
                     : featuresData?.volumes?.length   ? featuresData as any
                     : null;

        if (source) {
            setData({
                targetRoi:     source.targetRoi     || '-',
                selectedModel: source.selectedModel || '-',
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
            onSave={handleSaveResults}
            isLoading={isLoading}
            featuresData={featuresData}
            importanceData={importanceData}
        />
    );
}