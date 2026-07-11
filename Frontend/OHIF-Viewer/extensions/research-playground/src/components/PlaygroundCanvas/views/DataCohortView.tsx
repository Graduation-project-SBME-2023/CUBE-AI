import React, { useState, useEffect, useRef } from 'react';
import { Users, Filter, Database, AlertTriangle, Check, Activity, Layers, Download, Upload } from 'lucide-react';
// --- FIXED HELPER COMPONENT ---
const PausableGif = ({ uid, isHovered }: { uid: string; isHovered: boolean }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const imgRef = useRef<HTMLImageElement>(null);
    const [failed, setFailed] = useState(false);

    const handleLoad = () => {
        if (imgRef.current && canvasRef.current) {
            const img = imgRef.current;
            const canvas = canvasRef.current;
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            const ctx = canvas.getContext('2d');
            if (ctx) {
                ctx.drawImage(img, 0, 0);
            }
        }
    };

    if (failed) {
        return (
            <div className="relative w-full h-full flex items-center justify-center bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-[#1f2d40] via-[#0b1015] to-black">
                <Database size={24} className="text-[#3c5d80]" />
            </div>
        );
    }

    return (
        // Added bg-black and specific z-indexes to prevent any transparency bleed during zoom
        <div className="relative w-full h-full overflow-hidden bg-black">
            {/* Frozen first-frame canvas */}
            <canvas
                ref={canvasRef}
                className="absolute inset-0 w-full h-full object-cover z-10 pointer-events-none"
                style={{
                    opacity: isHovered ? 0 : 0.5,
                    transition: 'opacity 0.3s ease-in-out',
                }}
            />
            {/* Animated GIF */}
            <img
                ref={imgRef}
                src={`http://localhost:8000/volumes/${uid}/preview.gif`}
                alt={`Volume ${uid}`}
                className="absolute inset-0 w-full h-full object-cover z-20"
                style={{
                    opacity: isHovered ? 0.9 : 0,
                    transition: 'opacity 0.3s ease-in-out',
                }}
                onLoad={handleLoad}
                onError={() => setFailed(true)}
            />
        </div>
    );
};

// Cast icons to avoid TS "not assignable" errors on JSX usage
const CheckIcon: any = Check;
const AlertTriangleIcon: any = AlertTriangle;
const FilterIcon: any = Filter;
const DatabaseIcon: any = Database;
const ActivityIcon: any = Activity;
const LayersIcon: any = Layers;

interface PopulationData {
    agent_id?: string;
    status?: string;
    outputs?: {
        metrics?: {
            cohort_size?: number;
            match_rate?: number;
            inclusion_criteria?: string[];
            exclusion_criteria?: string[];
        };
        summary?: string;
        files?: Record<string, string>;
    };
    tool?: string;
    count?: number;
    uid_list?: string[];
    inclusion_criteria?: string[];
    exclusion_criteria?: string[];
    modality?: string;
    data?: any;
}

interface SegmentationData {
    agent_id?: string;
    status?: string;
    outputs?: {
        metrics?: {
            segmentation_method?: string;
            volumes_segmented?: number;
            roi_description?: string;
        };
        summary?: string;
        files?: Record<string, string>;
    };
    tool?: string;
    data?: any;
}

interface DataCohortViewProps {
    populationData?: PopulationData | null;
    segmentationData?: SegmentationData | null;
    sessionId?: string | null;
}

export default function DataCohortView({ populationData, segmentationData, sessionId }: DataCohortViewProps) {

    const [accumulatedPop, setAccumulatedPop] = useState({
        count: null as number | null,
        uids: [] as string[],
        inclusion: [] as string[],
        exclusion: [] as string[],
        modality: 'Awaiting...',
        summary: '',
        matchRate: null as number | null,
    });

    const [hoveredUid, setHoveredUid] = useState<string | null>(null);

    useEffect(() => {
        if (!populationData) return;

        setAccumulatedPop(prev => {
            const next = { ...prev };

            if (populationData.tool === 'write_inclusion_criteria') {
                if (populationData.inclusion_criteria) {
                    next.inclusion = Array.isArray(populationData.inclusion_criteria) ? populationData.inclusion_criteria : [populationData.inclusion_criteria as any];
                }
                if (populationData.exclusion_criteria) {
                    next.exclusion = Array.isArray(populationData.exclusion_criteria) ? populationData.exclusion_criteria : [populationData.exclusion_criteria as any];
                }
                if (populationData.modality) next.modality = populationData.modality;
            }

            if (populationData.tool === 'search_volumes_by_description') {
                if (populationData.count !== undefined) next.count = populationData.count;
                if (populationData.uid_list) next.uids = populationData.uid_list;
            }

            if (populationData.outputs?.metrics) {
                const metrics = populationData.outputs.metrics;
                if (metrics.cohort_size) next.count = metrics.cohort_size;
                if (metrics.match_rate) next.matchRate = metrics.match_rate;
                if (metrics.inclusion_criteria) {
                    next.inclusion = Array.isArray(metrics.inclusion_criteria) ? metrics.inclusion_criteria : [metrics.inclusion_criteria as any];
                }
                if (metrics.exclusion_criteria) {
                    next.exclusion = Array.isArray(metrics.exclusion_criteria) ? metrics.exclusion_criteria : [metrics.exclusion_criteria as any];
                }
                if (populationData.outputs.summary) next.summary = populationData.outputs.summary;
            }

            return next;
        });
    }, [populationData]);

    const segMetrics = segmentationData?.outputs?.metrics;
    const volumesSegmented = segMetrics?.volumes_segmented ?? null;
    const segMethod = segMetrics?.segmentation_method ?? null;
    const roiDesc = segMetrics?.roi_description ?? null;
    const hasPopData = populationData && populationData.status !== undefined;
    const hasSegData = segmentationData && segmentationData.status !== undefined;

    const [labellingMode, setLabellingMode] = useState<string>('automatic');

    useEffect(() => {
        const activeSessionId = sessionStorage.getItem('active_cube_session');
        if (activeSessionId) {
            fetch(`http://localhost:8150/session/${activeSessionId}/config`)
                .then(res => res.json())
                .then(data => {
                    if (data.labelling_mode) {
                        setLabellingMode(data.labelling_mode);
                    }
                    if (data.datasetlist && Array.isArray(data.datasetlist) && data.datasetlist.length > 0) {
                        setAccumulatedPop(prev => ({
                            ...prev,
                            uids: data.datasetlist,
                            count: data.datasetlist.length
                        }));
                    }
                })
                .catch(console.error);
        }

        const handleModeChange = (e: any) => {
            setLabellingMode(e.detail);
        };
        window.addEventListener('labellingModeChanged', handleModeChange);
        return () => window.removeEventListener('labellingModeChanged', handleModeChange);
    }, [populationData]);

    // --- NEW: File upload refs and state ---
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);

    // --- NEW: DOWNLOAD TEMPLATE LOGIC ---
    const handleDownloadCSV = () => {
        const header = "id,target\n";
        const rows = accumulatedPop.uids.map(uid => `${uid},`).join('\n');
        const csvContent = "data:text/csv;charset=utf-8," + header + rows;

        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", "cohort_labels_template.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    // --- UPLOAD FILLED CSV LOGIC ---
    const handleUploadCSV = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        // 1. Grab the session ID that PlaygroundCanvas saved
        const activeSessionId = sessionStorage.getItem('active_cube_session');

        if (!activeSessionId) {
            alert('Error: No active session found. Please start planning first.');
            if (fileInputRef.current) fileInputRef.current.value = '';
            return;
        }

        setIsUploading(true);
        const formData = new FormData();
        formData.append('file', file);

        try {
            // 2. Attach it to the URL query parameter
            const response = await fetch(`http://localhost:8150/populations/upload-targets?session_id=${activeSessionId}`, {
                method: 'POST',
                body: formData,
            });

            if (response.ok) {
                alert('Labels uploaded successfully! The agent will now continue.');
            } else {
                alert('Failed to upload labels.');
            }
        } catch (error) {
            console.error("Error uploading file", error);
            alert("Upload failed.");
        } finally {
            setIsUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    return (
        <div className="p-6 w-full h-full flex gap-6 bg-[#000000] overflow-hidden">

            {/* ── LEFT: Criteria Panel ── */}
            <div className="w-80 flex flex-col gap-4 shrink-0 overflow-y-auto pb-4 pr-1
                [&::-webkit-scrollbar]:w-2
                [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">

                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col gap-4">
                    <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs flex items-center gap-2">
                        <FilterIcon size={14} /> Study Criteria
                    </h3>

                    {accumulatedPop.modality && accumulatedPop.modality !== 'Awaiting...' && (
                        <div className="text-xs bg-[#3c5d80]/20 border border-[#3c5d80] text-[#91b9cd] px-2 py-1 rounded inline-block w-max">
                            Target: <span className="font-bold text-white">{accumulatedPop.modality}</span>
                        </div>
                    )}

                    {/* Inclusion */}
                    <div className="space-y-2">
                        <span className="text-[#6fe5b8] text-[10px] font-bold uppercase tracking-wide bg-[#6fe5b8]/10 px-2 py-0.5 rounded border border-[#6fe5b8]/20 inline-block">
                            Inclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1 min-h-[60px] max-h-[200px] overflow-y-auto
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                            [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
                            {accumulatedPop.inclusion.length > 0 ? (
                                accumulatedPop.inclusion.map((c, i) => (
                                    <div key={i} className="flex items-start gap-2">
                                        <CheckIcon size={12} className="text-[#6fe5b8] mt-0.5 shrink-0" />
                                        <span>{c}</span>
                                    </div>
                                ))
                            ) : (
                                <div className="text-gray-600 italic">Waiting for agent to define criteria...</div>
                            )}
                        </div>
                    </div>

                    {/* Exclusion */}
                    <div className="space-y-2">
                        <span className="text-[#ff6b6b] text-[10px] font-bold uppercase tracking-wide bg-[#ff6b6b]/10 px-2 py-0.5 rounded border border-[#ff6b6b]/20 inline-block">
                            Exclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1 min-h-[60px] max-h-[200px] overflow-y-auto
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                            [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
                            {accumulatedPop.exclusion.length > 0 ? (
                                accumulatedPop.exclusion.map((c, i) => (
                                    <div key={i} className="flex items-start gap-2">
                                        <AlertTriangleIcon size={12} className="text-[#ff6b6b] mt-0.5 shrink-0" />
                                        <span>{c}</span>
                                    </div>
                                ))
                            ) : (
                                <div className="text-gray-600 italic">Waiting for exclusion logic...</div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Cohort Summary */}
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 shrink-0">
                    <h3 className="text-[#91b9cd] font-bold uppercase tracking-wider text-xs mb-2">Cohort Summary</h3>
                    <div className="text-3xl font-bold text-white mb-1 transition-all">
                        {accumulatedPop.count ?? '—'}{' '}
                        <span className="text-sm font-normal text-gray-500">patients</span>
                    </div>
                    {accumulatedPop.matchRate !== null && (
                        <>
                            <div className="h-2 w-full bg-[#0b1015] rounded-full overflow-hidden mt-2">
                                <div
                                    className="h-full bg-[#5acce6] transition-all duration-1000"
                                    style={{ width: `${accumulatedPop.matchRate}%` }}
                                />
                            </div>
                            <div className="text-[10px] text-gray-500 mt-1 text-right">
                                {accumulatedPop.matchRate}% Match Rate
                            </div>
                        </>
                    )}
                    {accumulatedPop.summary && (
                        <p className="text-xs text-[#5acce6] mt-3 p-2 bg-[#0b1015] border border-[#1f2d40] rounded leading-relaxed">
                            {accumulatedPop.summary}
                        </p>
                    )}
                </div>

                {/* Segmentation Summary */}
                {hasSegData && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 shrink-0">
                        <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs mb-2 flex items-center gap-2">
                            <LayersIcon size={14} /> Segmentation
                        </h3>
                        {segMethod && (
                            <div className="text-xs text-gray-300 mb-1">
                                <span className="text-gray-500">Method:</span> {segMethod}
                            </div>
                        )}
                        {volumesSegmented !== null && (
                            <div className="text-2xl font-bold text-white">
                                {volumesSegmented}{' '}
                                <span className="text-sm font-normal text-gray-500">volumes</span>
                            </div>
                        )}
                        {roiDesc && <div className="text-xs text-gray-400 mt-1">{roiDesc}</div>}
                    </div>
                )}
            </div>

            {/* ── RIGHT: Data Grid ── */}
            <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                <div className="flex justify-between items-center mb-6">
                    <div>
                        <h2 className="text-lg font-bold text-white flex items-center gap-2">
                            <DatabaseIcon size={18} className="text-[#5acce6]" /> Data Acquisition
                        </h2>
                        <p className="text-xs text-gray-400 mt-1">
                            {hasPopData
                                ? `Agent Activity: Processing ${populationData?.tool?.replace(/_/g, ' ') || 'finalizing'}...`
                                : 'Waiting for Population Agent to initiate DB search...'}
                        </p>
                    </div>
                    <div className="flex gap-2 items-center text-xs font-mono">
                        {/* --- NEW: Download & Upload Buttons --- */}
                        {labellingMode !== 'automatic' && (
                            <div className="flex gap-2 mr-4">
                                <button
                                    onClick={handleDownloadCSV}
                                    className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1f2d40] hover:bg-[#3c5d80] text-[#91b9cd] hover:text-white border border-[#3c5d80] rounded transition-colors"
                                >
                                    <Download size={14} /> Template
                                </button>

                                <input
                                    type="file"
                                    accept=".csv"
                                    ref={fileInputRef}
                                    className="hidden"
                                    onChange={handleUploadCSV}
                                />

                                <button
                                    onClick={() => fileInputRef.current?.click()}
                                    disabled={isUploading}
                                    className={`flex items-center gap-1.5 px-3 py-1.5 border rounded transition-colors shadow-[0_0_10px_rgba(92,230,176,0.2)] ${isUploading
                                            ? 'bg-[#152e2e] text-gray-400 border-gray-600 cursor-not-allowed'
                                            : 'bg-[#152e2e] hover:bg-[#1f4242] text-[#5ce6b0] border-[#234d4d]'
                                        }`}
                                >
                                    <Upload size={14} /> {isUploading ? 'Uploading...' : 'Upload Labels'}
                                </button>
                            </div>
                        )}

                        {accumulatedPop.uids.length > 0 && (
                            <span className="px-3 py-1.5 bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d] rounded flex items-center gap-2 shadow-[0_0_10px_rgba(92,230,176,0.2)]">
                                <ActivityIcon size={12} className="animate-pulse" /> Cohort Secured
                            </span>
                        )}
                    </div>
                </div>

                {/* ── Dynamic Data Grid ── */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 overflow-y-auto pr-2 pb-4
                    [&::-webkit-scrollbar]:w-2
                    [&::-webkit-scrollbar-track]:bg-[#151a1f] [&::-webkit-scrollbar-track]:rounded-full
                    [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                    hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">

                    {accumulatedPop.uids.length > 0 ? (
                        accumulatedPop.uids.map(uid => (
                            /*
                             * ZOOM RESISTANT FIX
                             * ─────────────────────────────────────────────────────────────
                             * Using paddingTop: '100%' forces a perfect square regardless of browser
                             * sub-pixel zoom rendering. The content goes inside an absolute inset container.
                             */
                            <div
                                key={uid}
                                className="w-full bg-[#05080a] rounded border border-[#3c5d80]/50 hover:border-[#5acce6] transition-all group overflow-hidden relative block"
                                style={{ paddingTop: '100%' }}
                                onMouseEnter={() => setHoveredUid(uid)}
                                onMouseLeave={() => setHoveredUid(null)}
                            >
                                {/* Absolute wrapper that fills the rigid square aspect ratio */}
                                <div className="absolute inset-0 flex flex-col">
                                    {/* Image area — flex-1 + min-h-0 fills remaining card height */}
                                    <div className="relative w-full flex-1 min-h-0 overflow-hidden bg-black">
                                        <PausableGif uid={uid} isHovered={hoveredUid === uid} />

                                        {/* Status Badge */}
                                        <div className="absolute top-2 right-2 z-30 pointer-events-none">
                                            <div className="bg-[#152e2e]/90 backdrop-blur text-[#5ce6b0] border border-[#5ce6b0]/30 text-[8px] font-bold px-1.5 py-0.5 rounded uppercase shadow-lg">
                                                READY
                                            </div>
                                        </div>
                                    </div>

                                    {/* UID Footer — fixed 28 px, never grows or shrinks */}
                                    <div className="w-full h-7 shrink-0 bg-[#0b1015] border-t border-[#1f2d40] flex items-center justify-center px-2 group-hover:bg-[#151a1f] transition-colors relative z-20">
                                        <span
                                            className="text-[10px] font-mono text-[#91b9cd] truncate w-full text-center"
                                            title={uid}
                                        >
                                            ...{uid.slice(-12)}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        ))
                    ) : (
                        /* Skeleton loading state */
                        Array.from({ length: 12 }).map((_, i) => (
                            <div
                                key={`skeleton-${i}`}
                                className="w-full bg-[#0b1015] rounded border border-[#1f2d40] overflow-hidden animate-pulse relative block"
                                style={{ paddingTop: '100%' }}
                            >
                                <div className="absolute inset-0 flex items-center justify-center">
                                    <span className="text-[10px] font-mono text-gray-700">Awaiting Data</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}