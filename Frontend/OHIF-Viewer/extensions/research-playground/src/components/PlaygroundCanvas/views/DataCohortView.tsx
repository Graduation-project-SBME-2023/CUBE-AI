import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { 
    Users, Filter, Database, AlertTriangle, Check, Activity, Layers, 
    Table, Tag, CheckCircle2, Save, ShieldCheck, Search, Info, RefreshCw, Lock, AlertCircle, Upload
} from 'lucide-react';
import { useSnackbar } from '@ohif/ui';
import { getBackendApiBaseUrl, getAgentsApiBaseUrl } from '../../../utils/apiBase';

// Cast icons to avoid TS "not assignable" errors on JSX usage
const CheckIcon: any = Check;
const AlertTriangleIcon: any = AlertTriangle;
const FilterIcon: any = Filter;
const DatabaseIcon: any = Database;
const ActivityIcon: any = Activity;
const LayersIcon: any = Layers;
const TableIcon: any = Table;
const TagIcon: any = Tag;
const CheckCircleIcon: any = CheckCircle2;
const SaveIcon: any = Save;
const ShieldCheckIcon: any = ShieldCheck;
const SearchIcon: any = Search;
const InfoIcon: any = Info;
const RefreshCwIcon: any = RefreshCw;
const LockIcon: any = Lock;
const AlertCircleIcon: any = AlertCircle;
const UploadIcon: any = Upload;

// --- PAUSABLE GIF PREVIEW COMPONENT ---
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
                src={`${getBackendApiBaseUrl()}/volumes/${uid}/preview.gif`}
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
    /** Blocks all rendering and data fetching until the study plan has been approved by the user. */
    isPlanApproved?: boolean;
}

interface OrthancVolume {
    volume_series_instance_uid: string;
    study_uid: string;
    series_description?: string;
    modality?: string;
    num_slices?: number;
    body_part_examined?: string;
    orthanc_series_id?: string;
}

interface LabelRecord {
    patient_id: string;
    has_target_disease: string;
    confidence?: string;
    requires_manual_review?: string | boolean;
    source_evidence?: string;
}

export default function DataCohortView({ populationData, segmentationData, sessionId, isPlanApproved = false }: DataCohortViewProps) {
    const { show } = useSnackbar();

    // Active Subpage State
    const [subpage, setSubpage] = useState<'overview' | 'browser' | 'labeling' | 'approval'>('overview');

    // Population Pipeline State
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
    const [labellingMode, setLabellingMode] = useState<string>('automatic');
    const [cohortApproved, setCohortApproved] = useState<boolean>(false);

    // Data Browser State (All Orthanc Volumes)
    const [allVolumes, setAllVolumes] = useState<OrthancVolume[]>([]);
    const [isLoadingVolumes, setIsLoadingVolumes] = useState<boolean>(false);
    const [browserSearchTerm, setBrowserSearchTerm] = useState<string>('');
    const [selectedModalityFilter, setSelectedModalityFilter] = useState<string>('all');

    // Patient Labeling State
    const [labels, setLabels] = useState<LabelRecord[]>([]);
    const [isSavingLabels, setIsSavingLabels] = useState<boolean>(false);
    const [isUploadingTargets, setIsUploadingTargets] = useState<boolean>(false);
    const [uploadedTargetInfo, setUploadedTargetInfo] = useState<{
        filename: string; targetColumn: string; rowCount: number;
    } | null>(null);
    const [isApproving, setIsApproving] = useState<boolean>(false);

    // Reset all local cohort state whenever the plan becomes freshly approved (new run)
    // or whenever the session changes — guarantees a clean slate.
    const prevIsPlanApprovedRef = useRef(isPlanApproved);
    useEffect(() => {
        const wasApproved = prevIsPlanApprovedRef.current;
        prevIsPlanApprovedRef.current = isPlanApproved;
        // Only reset when transitioning from not-approved → approved (new execution starting)
        if (!wasApproved && isPlanApproved) {
            setAccumulatedPop({ count: null, uids: [], inclusion: [], exclusion: [], modality: 'Awaiting...', summary: '', matchRate: null });
            setLabels([]);
            setCohortApproved(false);
            setSubpage('overview');
        }
    }, [isPlanApproved]);

    // Handle WebSocket / agent broadcast data updates
    useEffect(() => {
        if (!populationData) {
            setAccumulatedPop({
                count: null,
                uids: [],
                inclusion: [],
                exclusion: [],
                modality: 'Awaiting...',
                summary: '',
                matchRate: null,
            });
            return;
        }

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

    // Fetch session config and cohort state — only allowed after plan is approved
    const fetchCohortState = useCallback(async () => {
        // ⛔ Hard gate: never load old cohort data before the plan is approved
        if (!isPlanApproved) return;

        const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
        if (!activeSessionId) return;

        try {
            const res = await fetch(`${getAgentsApiBaseUrl()}/populations/cohort-state?session_id=${activeSessionId}`);
            if (res.ok) {
                const data = await res.json();
                if (data.labelling_mode) setLabellingMode(data.labelling_mode);
                if (data.cohort_approved !== undefined) setCohortApproved(data.cohort_approved);
                if (data.datasetlist && Array.isArray(data.datasetlist)) {
                    setAccumulatedPop(prev => ({
                        ...prev,
                        uids: data.datasetlist,
                        count: data.datasetlist.length
                    }));
                }
                if (data.labels && Array.isArray(data.labels)) {
                    setLabels(data.labels);
                }
            } else {
                // Fallback to older /config endpoint if needed
                const configRes = await fetch(`${getAgentsApiBaseUrl()}/session/${activeSessionId}/config`);
                if (configRes.ok) {
                    const cfg = await configRes.json();
                    if (cfg.labelling_mode) setLabellingMode(cfg.labelling_mode);
                    if (cfg.cohort_approved !== undefined) setCohortApproved(cfg.cohort_approved);
                    if (cfg.datasetlist && Array.isArray(cfg.datasetlist)) {
                        setAccumulatedPop(prev => ({ ...prev, uids: cfg.datasetlist, count: cfg.datasetlist.length }));
                    }
                }
            }
        } catch (err) {
            console.error('Failed to fetch cohort state:', err);
        }
    }, [isPlanApproved, sessionId]);

    useEffect(() => {
        // ⛔ Do not load any cohort data until plan is approved
        if (!isPlanApproved) return;

        fetchCohortState();

        const handleModeChange = (e: any) => {
            setLabellingMode(e.detail);
            fetchCohortState();
        };
        window.addEventListener('labellingModeChanged', handleModeChange);
        return () => window.removeEventListener('labellingModeChanged', handleModeChange);
    }, [isPlanApproved, populationData, subpage, fetchCohortState]);

    // Fetch all Orthanc volumes when opening Data Browser — only after plan approval
    const fetchAllVolumes = async () => {
        if (!isPlanApproved) return;
        setIsLoadingVolumes(true);
        try {
            const res = await fetch(`${getBackendApiBaseUrl()}/volumes/?limit=1000`);
            if (res.ok) {
                const vols: OrthancVolume[] = await res.json();
                setAllVolumes(vols);
            } else {
                console.error("Failed to fetch volumes from Orthanc");
            }
        } catch (err) {
            console.error("Error fetching Orthanc volumes:", err);
        } finally {
            setIsLoadingVolumes(false);
        }
    };

    useEffect(() => {
        if (subpage === 'browser' && isPlanApproved) {
            fetchAllVolumes();
        }
    }, [subpage, isPlanApproved]);

    // Ensure label record exists in local state for every selected volume UID
    const unifiedLabels = useMemo(() => {
        const map = new Map<string, LabelRecord>();
        labels.forEach(l => map.set(l.patient_id, l));

        return accumulatedPop.uids.map(uid => {
            const existing = map.get(uid);
            if (existing) return existing;
            return {
                patient_id: uid,
                has_target_disease: '',
                confidence: 'unlabeled',
                requires_manual_review: true,
                source_evidence: labellingMode === 'automatic'
                    ? 'No clinical report match found. Pending user selection.'
                    : 'Manual mode — awaiting doctor selection.'
            };
        });
    }, [labels, accumulatedPop.uids, labellingMode]);

    // Handle toggling volume inclusion in Data Browser
    const handleToggleVolumeInclusion = async (uid: string) => {
        const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
        if (!activeSessionId) {
            show({ title: 'Error', message: 'No active session found.', type: 'error', duration: 3000 });
            return;
        }

        const isIncluded = accumulatedPop.uids.includes(uid);
        const nextUids = isIncluded 
            ? accumulatedPop.uids.filter(id => id !== uid)
            : [...accumulatedPop.uids, uid];

        // Optimistically update local state
        setAccumulatedPop(prev => ({ ...prev, uids: nextUids, count: nextUids.length }));
        setCohortApproved(false); // Edits reset approval

        try {
            const res = await fetch(`${getAgentsApiBaseUrl()}/populations/update-cohort-list`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: activeSessionId, datasetlist: nextUids })
            });
            if (!res.ok) {
                show({ title: 'Error', message: 'Failed to update cohort list in backend.', type: 'error', duration: 4000 });
            }
        } catch (err) {
            console.error('Error updating cohort list:', err);
            show({ title: 'Network Error', message: 'Failed to communicate with backend.', type: 'error', duration: 4000 });
        }
    };

    // Handle saving patient label edits from Subpage 3
    const handleUpdateLabelValue = (uid: string, value: string) => {
        setLabels(prev => {
            const exists = prev.some(l => l.patient_id === uid);
            if (exists) {
                return prev.map(l => l.patient_id === uid ? { ...l, has_target_disease: value, confidence: 'manual override' } : l);
            } else {
                return [...prev, {
                    patient_id: uid,
                    has_target_disease: value,
                    confidence: 'manual override',
                    requires_manual_review: false,
                    source_evidence: 'Manually assigned in UI'
                }];
            }
        });
        setCohortApproved(false);
    };

    const handleSaveLabels = async () => {
        const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
        if (!activeSessionId) {
            show({ title: 'Error', message: 'No active session found.', type: 'error', duration: 3000 });
            return;
        }

        setIsSavingLabels(true);
        try {
            const res = await fetch(`${getAgentsApiBaseUrl()}/populations/save-labels`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: activeSessionId, labels: unifiedLabels })
            });
            if (res.ok) {
                show({ title: 'Success', message: 'Patient labels saved successfully!', type: 'success', duration: 4000 });
                setCohortApproved(false);
                await fetchCohortState();
            } else {
                show({ title: 'Error', message: 'Failed to save patient labels.', type: 'error', duration: 4000 });
            }
        } catch (err) {
            console.error('Error saving labels:', err);
            show({ title: 'Error', message: 'Network error while saving labels.', type: 'error', duration: 4000 });
        } finally {
            setIsSavingLabels(false);
        }
    };

    const handleTargetCsvUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const targetFile = event.target.files?.[0];
        event.target.value = '';
        if (!targetFile) return;
        const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
        if (!activeSessionId) {
            show({ title: 'Error', message: 'No active session found.', type: 'error', duration: 3000 });
            return;
        }
        if (!targetFile.name.toLowerCase().endsWith('.csv')) {
            show({ title: 'Invalid File', message: 'Please select a .csv target file.', type: 'error', duration: 4000 });
            return;
        }

        setIsUploadingTargets(true);
        try {
            const formData = new FormData();
            formData.append('file', targetFile);
            const response = await fetch(
                `${getAgentsApiBaseUrl()}/populations/upload-targets?session_id=${encodeURIComponent(activeSessionId)}`,
                { method: 'POST', body: formData }
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(body.detail || 'Target CSV validation failed.');
            }
            setUploadedTargetInfo({
                filename: targetFile.name,
                targetColumn: body.target_column,
                rowCount: body.row_count,
            });
            setCohortApproved(false);
            show({
                title: 'Target CSV Uploaded',
                message: `Loaded ${body.row_count} targets from column “${body.target_column}”.`,
                type: 'success',
                duration: 5000,
            });
        } catch (error) {
            show({
                title: 'Target Upload Failed',
                message: error instanceof Error ? error.message : 'Could not upload the target CSV.',
                type: 'error',
                duration: 6000,
            });
        } finally {
            setIsUploadingTargets(false);
        }
    };

    // Handle Cohort Approval (HITL Gate) from Subpage 4
    const handleApproveCohort = async () => {
        const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
        if (!activeSessionId) {
            show({ title: 'Error', message: 'No active session found.', type: 'error', duration: 3000 });
            return;
        }

        setIsApproving(true);
        try {
            const res = await fetch(`${getAgentsApiBaseUrl()}/populations/approve-cohort`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: activeSessionId })
            });
            const body = await res.json();
            if (res.ok) {
                setCohortApproved(true);
                show({ 
                    title: 'Cohort Approved!', 
                    message: `Successfully locked ${body.labeled_count} labeled volume(s). Segmentation pipeline continuing...`, 
                    type: 'success', 
                    duration: 6000 
                });
                await fetchCohortState();
            } else {
                show({ 
                    title: 'Cannot Approve Cohort', 
                    message: body.detail || 'Validation failed during cohort approval.', 
                    type: 'error', 
                    duration: 5000 
                });
            }
        } catch (err) {
            console.error('Error approving cohort:', err);
            show({ title: 'Network Error', message: 'Could not contact approval backend.', type: 'error', duration: 4000 });
        } finally {
            setIsApproving(false);
        }
    };

    // Filtered lists for Subpage 2 (Browser) and Subpage 4 (Final Cohort)
    const filteredBrowserVolumes = useMemo(() => {
        return allVolumes.filter(vol => {
            const matchesSearch = !browserSearchTerm || 
                vol.volume_series_instance_uid.toLowerCase().includes(browserSearchTerm.toLowerCase()) ||
                (vol.series_description || '').toLowerCase().includes(browserSearchTerm.toLowerCase()) ||
                (vol.body_part_examined || '').toLowerCase().includes(browserSearchTerm.toLowerCase());
            const matchesModality = selectedModalityFilter === 'all' || 
                (vol.modality || '').toLowerCase() === selectedModalityFilter.toLowerCase();
            return matchesSearch && matchesModality;
        });
    }, [allVolumes, browserSearchTerm, selectedModalityFilter]);

    const finalLabeledCohort = useMemo(() => {
        return unifiedLabels.filter(l => {
            const val = l.has_target_disease?.toString().trim().toLowerCase();
            return val === 'true' || val === 'false' || val === '1' || val === '0';
        });
    }, [unifiedLabels]);

    const posCount = finalLabeledCohort.filter(l => {
        const val = l.has_target_disease?.toString().trim().toLowerCase();
        return val === 'true' || val === '1';
    }).length;
    const negCount = finalLabeledCohort.length - posCount;
    const canApprove = accumulatedPop.uids.length > 0 && finalLabeledCohort.length > 0;

    // ⛔ PLAN APPROVAL GATE — show nothing until the user approves the study plan
    if (!isPlanApproved) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center gap-6 bg-[#000000] text-white select-none">
                {/* Spinner */}
                <div className="relative flex items-center justify-center">
                    <div className="absolute w-20 h-20 rounded-full border-2 border-[#1a2a3a] opacity-40" />
                    <div
                        className="w-20 h-20 rounded-full border-2 border-transparent"
                        style={{
                            borderTopColor: '#5acce6',
                            borderRightColor: '#3c5d8040',
                            animation: 'spin 1.1s linear infinite',
                        }}
                    />
                    <Database size={22} className="absolute text-[#3c6d8a]" />
                </div>

                {/* Label */}
                <div className="flex flex-col items-center gap-2">
                    <p className="text-[15px] font-semibold text-[#91b9cd] tracking-wide">
                        Awaiting Plan Approval
                    </p>
                    <p className="text-xs text-[#4a6a80] max-w-[280px] text-center leading-relaxed">
                        The Data &amp; Population tab will be available once you review and approve
                        the generated study plan.
                    </p>
                </div>

                <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
        );
    }

    return (
        <div className="p-6 w-full h-full flex flex-col gap-6 bg-[#000000] overflow-hidden text-white">

            {/* ── TOP SUB-NAVIGATION TABS ── */}
            <div className="flex items-center justify-between border-b border-[#1f2d40] pb-2 px-1 shrink-0">
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => setSubpage('overview')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border ${
                            subpage === 'overview'
                                ? 'bg-[#152433] text-[#5acce6] border-[#3c5d80] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                                : 'bg-[#0b1015] text-[#91b9cd] hover:text-white border-[#1f2d40] hover:border-[#3c5d80]'
                        }`}
                    >
                        <DatabaseIcon size={15} /> Overview
                        <span className="ml-1 bg-[#05080a] px-2 py-0.5 rounded text-[10px] text-gray-400 border border-[#1f2d40]">
                            {accumulatedPop.count ?? 0}
                        </span>
                    </button>

                    <button
                        onClick={() => setSubpage('browser')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border ${
                            subpage === 'browser'
                                ? 'bg-[#152433] text-[#5acce6] border-[#3c5d80] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                                : 'bg-[#0b1015] text-[#91b9cd] hover:text-white border-[#1f2d40] hover:border-[#3c5d80]'
                        }`}
                    >
                        <TableIcon size={15} /> Data Browser (Table)
                    </button>

                    <button
                        onClick={() => setSubpage('labeling')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border ${
                            subpage === 'labeling'
                                ? 'bg-[#152433] text-[#5acce6] border-[#3c5d80] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                                : 'bg-[#0b1015] text-[#91b9cd] hover:text-white border-[#1f2d40] hover:border-[#3c5d80]'
                        }`}
                    >
                        <TagIcon size={15} /> Patient Labeling
                        <span className="ml-1 bg-[#05080a] px-2 py-0.5 rounded text-[10px] border border-[#1f2d40]"
                              style={{ color: finalLabeledCohort.length === accumulatedPop.uids.length && accumulatedPop.uids.length > 0 ? '#6fe5b8' : '#ffcf6a' }}>
                            {finalLabeledCohort.length} / {accumulatedPop.uids.length}
                        </span>
                    </button>

                    <button
                        onClick={() => setSubpage('approval')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border ${
                            subpage === 'approval'
                                ? 'bg-[#152433] text-[#5acce6] border-[#3c5d80] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                                : 'bg-[#0b1015] text-[#91b9cd] hover:text-white border-[#1f2d40] hover:border-[#3c5d80]'
                        }`}
                    >
                        <ShieldCheckIcon size={15} /> Cohort Approval Gate
                        {cohortApproved ? (
                            <span className="ml-1 bg-[#152e2e] text-[#5ce6b0] px-2 py-0.5 rounded text-[10px] font-bold border border-[#234d4d]">
                                APPROVED
                            </span>
                        ) : (
                            <span className="ml-1 bg-[#2e2615] text-[#ffcf6a] px-2 py-0.5 rounded text-[10px] font-bold border border-[#4d4223]">
                                PENDING
                            </span>
                        )}
                    </button>
                </div>

                <div className="flex items-center gap-3 text-xs font-mono">
                    <span className="text-gray-400">Labelling Mode: <strong className="text-[#5acce6] uppercase">{labellingMode}</strong></span>
                    <button 
                        onClick={() => fetchCohortState()} 
                        title="Refresh Cohort State"
                        className="p-1.5 bg-[#0b1015] hover:bg-[#1f2d40] text-[#91b9cd] rounded border border-[#1f2d40] transition-colors"
                    >
                        <RefreshCwIcon size={14} />
                    </button>
                </div>
            </div>

            {/* ── SUBPAGE 1: OVERVIEW ── */}
            {subpage === 'overview' && (
                <div className="w-full flex-1 flex gap-6 min-h-0 overflow-hidden">
                    {/* LEFT: Criteria & Summaries */}
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

                    {/* RIGHT: Selected Data Grid */}
                    <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                        <div className="flex justify-between items-center mb-6">
                            <div>
                                <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                    <DatabaseIcon size={18} className="text-[#5acce6]" /> Data Acquisition Preview
                                </h2>
                                <p className="text-xs text-gray-400 mt-1">
                                    {hasPopData
                                        ? `Agent Activity: Processing ${populationData?.tool?.replace(/_/g, ' ') || 'finalizing'}...`
                                        : 'Waiting for Population Agent to initiate DB search...'}
                                </p>
                            </div>
                            <div className="flex gap-2 items-center text-xs font-mono">
                                {cohortApproved ? (
                                    <span className="px-3 py-1.5 bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d] rounded flex items-center gap-2 shadow-[0_0_10px_rgba(92,230,176,0.2)]">
                                        <ActivityIcon size={12} className="animate-pulse" /> Cohort Secured & Approved
                                    </span>
                                ) : (
                                    <span className="px-3 py-1.5 bg-[#2e2615] text-[#ffcf6a] border border-[#4d4223] rounded flex items-center gap-2 shadow-[0_0_10px_rgba(255,207,106,0.15)]">
                                        <AlertTriangleIcon size={12} /> Pending Cohort Approval
                                    </span>
                                )}
                            </div>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 overflow-y-auto pr-2 pb-4
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:bg-[#151a1f] [&::-webkit-scrollbar-track]:rounded-full
                            [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">

                            {accumulatedPop.uids.length > 0 ? (
                                accumulatedPop.uids.map(uid => {
                                    const isLabeled = unifiedLabels.some(l => l.patient_id === uid && (l.has_target_disease === 'true' || l.has_target_disease === 'false' || l.has_target_disease === '1' || l.has_target_disease === '0'));
                                    return (
                                        <div
                                            key={uid}
                                            className="w-full bg-[#05080a] rounded border border-[#3c5d80]/50 hover:border-[#5acce6] transition-all group overflow-hidden relative block"
                                            style={{ paddingTop: '100%' }}
                                            onMouseEnter={() => setHoveredUid(uid)}
                                            onMouseLeave={() => setHoveredUid(null)}
                                        >
                                            <div className="absolute inset-0 flex flex-col">
                                                <div className="relative w-full flex-1 min-h-0 overflow-hidden bg-black">
                                                    <PausableGif uid={uid} isHovered={hoveredUid === uid} />
                                                    <div className="absolute top-2 right-2 z-30 pointer-events-none">
                                                        {isLabeled ? (
                                                            <div className="bg-[#152e2e]/90 backdrop-blur text-[#5ce6b0] border border-[#5ce6b0]/30 text-[8px] font-bold px-1.5 py-0.5 rounded uppercase shadow-lg">
                                                                READY
                                                            </div>
                                                        ) : (
                                                            <div className="bg-[#2e2615]/90 backdrop-blur text-[#ffcf6a] border border-[#ffcf6a]/30 text-[8px] font-bold px-1.5 py-0.5 rounded uppercase shadow-lg">
                                                                PENDING LABEL
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                                <div className="w-full h-7 shrink-0 bg-[#0b1015] border-t border-[#1f2d40] flex items-center justify-center px-2 group-hover:bg-[#151a1f] transition-colors relative z-20">
                                                    <span className="text-[10px] font-mono text-[#91b9cd] truncate w-full text-center" title={uid}>
                                                        ...{uid.slice(-12)}
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })
                            ) : (
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
            )}

            {/* ── SUBPAGE 2: DATA BROWSER (TABLE VIEW) ── */}
            {subpage === 'browser' && (
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0 gap-4">
                    <div className="flex flex-wrap justify-between items-center gap-4 border-b border-[#1f2d40] pb-4">
                        <div>
                            <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                <TableIcon size={18} className="text-[#5acce6]" /> PACS Data Browser
                            </h2>
                            <p className="text-xs text-gray-400 mt-1">
                                Review and toggle inclusion for all imaging datasets available in Orthanc PACS.
                            </p>
                        </div>

                        <div className="flex items-center gap-3">
                            {/* Search filter */}
                            <div className="relative w-64">
                                <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                                <input
                                    type="text"
                                    value={browserSearchTerm}
                                    onChange={(e) => setBrowserSearchTerm(e.target.value)}
                                    placeholder="Search organ, description, UID..."
                                    className="w-full bg-[#0b1015] border border-[#1f2d40] rounded-lg pl-8 pr-3 py-1.5 text-xs text-white focus:outline-none focus:border-[#5acce6]"
                                />
                            </div>

                            {/* Modality filter */}
                            <select
                                value={selectedModalityFilter}
                                onChange={(e) => setSelectedModalityFilter(e.target.value)}
                                className="bg-[#0b1015] border border-[#1f2d40] rounded-lg px-3 py-1.5 text-xs text-[#91b9cd] focus:outline-none focus:border-[#5acce6]"
                            >
                                <option value="all">All Modalities</option>
                                <option value="CT">CT</option>
                                <option value="MR">MR / MRI</option>
                                <option value="PT">PET</option>
                                <option value="CR">CR</option>
                            </select>
                        </div>
                    </div>

                    {isLoadingVolumes ? (
                        <div className="flex-1 flex flex-col items-center justify-center text-gray-500 text-xs gap-3">
                            <RefreshCwIcon size={24} className="animate-spin text-[#5acce6]" />
                            Fetching available imaging data from Orthanc...
                        </div>
                    ) : (
                        <div className="flex-1 overflow-y-auto border border-[#1f2d40] rounded-lg bg-[#0b1015]
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                            [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
                            
                            <table className="w-full text-left border-collapse">
                                <thead className="bg-[#152433] sticky top-0 z-20 text-[11px] font-bold text-[#5acce6] uppercase tracking-wider border-b border-[#3c5d80]">
                                    <tr>
                                        <th className="py-3 px-4 w-12 text-center">Include</th>
                                        <th className="py-3 px-4">Series Instance UID</th>
                                        <th className="py-3 px-4">Organ / Body Part</th>
                                        <th className="py-3 px-4">Modality</th>
                                        <th className="py-3 px-4">Series Description</th>
                                        <th className="py-3 px-4 w-24 text-right">Slices</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-[#1f2d40] text-xs">
                                    {filteredBrowserVolumes.length > 0 ? (
                                        filteredBrowserVolumes.map((vol, index) => {
                                            const isChecked = accumulatedPop.uids.includes(vol.volume_series_instance_uid);
                                            return (
                                                <tr 
                                                    key={vol.volume_series_instance_uid || index}
                                                    className={`hover:bg-[#151a1f]/80 transition-colors cursor-pointer ${isChecked ? 'bg-[#152433]/30' : ''}`}
                                                    onClick={() => handleToggleVolumeInclusion(vol.volume_series_instance_uid)}
                                                >
                                                    <td className="py-3 px-4 text-center" onClick={(e) => e.stopPropagation()}>
                                                        <input
                                                            type="checkbox"
                                                            checked={isChecked}
                                                            onChange={() => handleToggleVolumeInclusion(vol.volume_series_instance_uid)}
                                                            className="w-4 h-4 rounded bg-[#0b1015] border-[#3c5d80] text-[#5acce6] focus:ring-0 cursor-pointer accent-[#5acce6]"
                                                        />
                                                    </td>
                                                    <td className="py-3 px-4 font-mono text-[#91b9cd]" title={vol.volume_series_instance_uid}>
                                                        {vol.volume_series_instance_uid ? `...${vol.volume_series_instance_uid.slice(-20)}` : 'N/A'}
                                                    </td>
                                                    <td className="py-3 px-4 font-semibold text-white">
                                                        {vol.body_part_examined || <span className="text-gray-600 italic">Unspecified</span>}
                                                    </td>
                                                    <td className="py-3 px-4">
                                                        <span className="bg-[#1f2d40] text-[#5acce6] px-2 py-0.5 rounded font-mono text-[10px]">
                                                            {vol.modality || '—'}
                                                        </span>
                                                    </td>
                                                    <td className="py-3 px-4 text-gray-300">
                                                        {vol.series_description || <span className="text-gray-600 italic">No series description</span>}
                                                    </td>
                                                    <td className="py-3 px-4 text-right font-mono text-gray-400">
                                                        {vol.num_slices ?? '—'}
                                                    </td>
                                                </tr>
                                            );
                                        })
                                    ) : (
                                        <tr>
                                            <td colSpan={6} className="py-12 text-center text-gray-500 italic">
                                                No volumes matched your filter criteria in Orthanc PACS.
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* ── SUBPAGE 3: PATIENT LABELING (IN-APP) ── */}
            {subpage === 'labeling' && (
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0 gap-4">
                    <div className="flex flex-wrap justify-between items-center gap-4 border-b border-[#1f2d40] pb-4">
                        <div>
                            <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                <TagIcon size={18} className="text-[#5acce6]" /> Interactive Patient Labeling
                            </h2>
                            <p className="text-xs text-gray-400 mt-1">
                                Assign classification labels here, or upload a CSV for continuous regression targets.
                            </p>
                        </div>

                        <div className="flex items-center gap-2 flex-wrap">
                            <label className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border cursor-pointer ${
                                isUploadingTargets
                                    ? 'border-[#3c5d80] text-gray-500 cursor-wait'
                                    : 'border-[#5acce6]/40 bg-[#152433] text-[#5acce6] hover:border-[#5acce6]'
                            }`}>
                                <UploadIcon size={14} />
                                {isUploadingTargets ? 'Uploading...' : 'Upload Target CSV'}
                                <input type="file" accept=".csv,text/csv" className="hidden"
                                    disabled={isUploadingTargets} onChange={handleTargetCsvUpload} />
                            </label>
                            <button
                                onClick={handleSaveLabels}
                                disabled={isSavingLabels}
                                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border shadow-lg ${
                                    isSavingLabels
                                        ? 'bg-[#152e2e]/50 text-gray-500 border-gray-700 cursor-not-allowed'
                                        : 'bg-[#152e2e] hover:bg-[#1f4242] text-[#5ce6b0] border-[#234d4d] shadow-[0_0_15px_rgba(92,230,176,0.2)]'
                                }`}
                            >
                                <SaveIcon size={14} />
                                {isSavingLabels ? 'Saving...' : 'Save & Apply Labels'}
                            </button>
                        </div>
                    </div>

                    <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] px-4 py-3 flex items-start gap-3">
                        <InfoIcon size={15} className="mt-0.5 text-[#5acce6] shrink-0" />
                        <div className="min-w-0">
                            <div className="text-xs font-semibold text-[#91b9cd]">Regression target file</div>
                            <p className="mt-1 text-[11px] text-[#566a84]">
                                Include one patient identifier column and one numeric outcome column named <span className="font-mono text-[#5acce6]">y</span> or <span className="font-mono text-[#5acce6]">target</span>. Missing targets and duplicate IDs are rejected before the pipeline resumes.
                            </p>
                            {uploadedTargetInfo && (
                                <div className="mt-2 text-[10px] font-mono text-[#6fe5b8]">
                                    {uploadedTargetInfo.filename} · {uploadedTargetInfo.rowCount} rows · target: {uploadedTargetInfo.targetColumn}
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="flex-1 overflow-y-auto border border-[#1f2d40] rounded-lg bg-[#0b1015]
                        [&::-webkit-scrollbar]:w-2
                        [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                        [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                        hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">

                        <table className="w-full text-left border-collapse">
                            <thead className="bg-[#152433] sticky top-0 z-20 text-[11px] font-bold text-[#5acce6] uppercase tracking-wider border-b border-[#3c5d80]">
                                <tr>
                                    <th className="py-3 px-4 w-44">Patient / Volume ID</th>
                                    <th className="py-3 px-4 w-52">Target Disease Label</th>
                                    <th className="py-3 px-4 w-40">Confidence</th>
                                    <th className="py-3 px-4">Clinical Evidence & Rationale</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-[#1f2d40] text-xs">
                                {unifiedLabels.length > 0 ? (
                                    unifiedLabels.map((row) => {
                                        const htd = row.has_target_disease?.toString().trim().toLowerCase();
                                        const isLowConf = row.confidence?.toLowerCase() === 'low' || row.confidence?.toLowerCase() === 'unlabeled';
                                        
                                        return (
                                            <tr key={row.patient_id} className="hover:bg-[#151a1f]/70 transition-colors">
                                                <td className="py-3 px-4 font-mono text-[#91b9cd]" title={row.patient_id}>
                                                    {row.patient_id ? (row.patient_id.length > 22 ? `...${row.patient_id.slice(-20)}` : row.patient_id) : 'Unknown'}
                                                </td>

                                                {/* Label selection pills */}
                                                <td className="py-3 px-4">
                                                    <div className="flex items-center gap-1 bg-[#05080a] p-1 rounded-lg border border-[#1f2d40] w-max">
                                                        <button
                                                            type="button"
                                                            onClick={() => handleUpdateLabelValue(row.patient_id, 'true')}
                                                            className={`px-3 py-1 rounded font-bold text-[11px] transition-colors ${
                                                                htd === 'true' || htd === '1'
                                                                    ? 'bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d]'
                                                                    : 'text-gray-400 hover:text-white'
                                                            }`}
                                                        >
                                                            Positive (+1)
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleUpdateLabelValue(row.patient_id, 'false')}
                                                            className={`px-3 py-1 rounded font-bold text-[11px] transition-colors ${
                                                                htd === 'false' || htd === '0'
                                                                    ? 'bg-[#331818] text-[#ff6b6b] border border-[#522424]'
                                                                    : 'text-gray-400 hover:text-white'
                                                            }`}
                                                        >
                                                            Negative (0)
                                                        </button>
                                                    </div>
                                                </td>

                                                {/* Confidence Badge with Alerting */}
                                                <td className="py-3 px-4">
                                                    {row.confidence?.toLowerCase() === 'high' && (
                                                        <span className="px-2 py-0.5 rounded bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d] font-mono text-[10px] uppercase">
                                                            High
                                                        </span>
                                                    )}
                                                    {row.confidence?.toLowerCase() === 'medium' && (
                                                        <span className="px-2 py-0.5 rounded bg-[#2e2615] text-[#ffcf6a] border border-[#4d4223] font-mono text-[10px] uppercase">
                                                            Medium
                                                        </span>
                                                    )}
                                                    {isLowConf && (
                                                        <span className="px-2 py-0.5 rounded bg-[#331818] text-[#ff6b6b] border border-[#522424] font-mono text-[10px] uppercase inline-flex items-center gap-1 font-bold animate-pulse">
                                                            <AlertCircleIcon size={11} /> {row.confidence || 'Low'}
                                                        </span>
                                                    )}
                                                    {(!row.confidence || (row.confidence.toLowerCase() !== 'high' && row.confidence.toLowerCase() !== 'medium' && !isLowConf)) && (
                                                        <span className="text-gray-500 font-mono text-[10px]">{row.confidence || '—'}</span>
                                                    )}
                                                </td>

                                                {/* Evidence / Orphan status */}
                                                <td className="py-3 px-4 text-gray-300 leading-relaxed">
                                                    {row.source_evidence ? (
                                                        <span>{row.source_evidence}</span>
                                                    ) : (
                                                        <span className="text-gray-600 italic">No automated evidence recorded.</span>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })
                                ) : (
                                    <tr>
                                        <td colSpan={4} className="py-12 text-center text-gray-500 italic">
                                            No study candidates available to label. Please wait for the Population Agent or add volumes via the Data Browser.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* ── SUBPAGE 4: FINAL COHORT & APPROVAL GATE ── */}
            {subpage === 'approval' && (
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0 gap-6">
                    {/* Summary statistics bar */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-[#0b1015] p-4 rounded-lg border border-[#1f2d40]">
                            <div className="text-[11px] text-gray-400 font-bold uppercase">Total Discovered</div>
                            <div className="text-2xl font-bold text-white mt-1">{accumulatedPop.uids.length}</div>
                        </div>
                        <div className="bg-[#0b1015] p-4 rounded-lg border border-[#1f2d40]">
                            <div className="text-[11px] text-[#6fe5b8] font-bold uppercase">Valid Labeled Cohort</div>
                            <div className="text-2xl font-bold text-[#5ce6b0] mt-1">{finalLabeledCohort.length}</div>
                        </div>
                        <div className="bg-[#0b1015] p-4 rounded-lg border border-[#1f2d40]">
                            <div className="text-[11px] text-[#5acce6] font-bold uppercase">Positive Targets (+1)</div>
                            <div className="text-2xl font-bold text-[#5acce6] mt-1">{posCount}</div>
                        </div>
                        <div className="bg-[#0b1015] p-4 rounded-lg border border-[#1f2d40]">
                            <div className="text-[11px] text-[#ff6b6b] font-bold uppercase">Negative Targets (0)</div>
                            <div className="text-2xl font-bold text-[#ff6b6b] mt-1">{negCount}</div>
                        </div>
                    </div>

                    {/* Final Approved Grid / List Preview */}
                    <div className="flex-1 min-h-0 overflow-y-auto border border-[#1f2d40] rounded-lg bg-[#0b1015] p-4
                        [&::-webkit-scrollbar]:w-2
                        [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar-track]:rounded-full
                        [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-thumb]:rounded-full
                        hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">

                        <h3 className="text-xs font-bold text-[#91b9cd] uppercase tracking-wider mb-4 flex items-center gap-2">
                            <CheckCircleIcon size={14} className="text-[#5ce6b0]" /> Final Approved Volume Candidates ({finalLabeledCohort.length})
                        </h3>

                        {finalLabeledCohort.length > 0 ? (
                            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                                {finalLabeledCohort.map(row => {
                                    const isPos = row.has_target_disease?.toString().trim().toLowerCase() === 'true';
                                    return (
                                        <div key={row.patient_id} className="bg-[#151a1f] border border-[#3c5d80]/40 rounded-lg p-2 flex flex-col items-center justify-between text-center">
                                            <span className="text-[11px] font-mono text-gray-300 truncate w-full" title={row.patient_id}>
                                                ...{row.patient_id.slice(-12)}
                                            </span>
                                            <div className="my-2">
                                                {isPos ? (
                                                    <span className="px-2 py-0.5 bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d] text-[10px] font-bold rounded">
                                                        POSITIVE (+1)
                                                    </span>
                                                ) : (
                                                    <span className="px-2 py-0.5 bg-[#331818] text-[#ff6b6b] border border-[#522424] text-[10px] font-bold rounded">
                                                        NEGATIVE (0)
                                                    </span>
                                                )}
                                            </div>
                                            <span className="text-[9px] text-gray-500 font-mono">Conf: {row.confidence || 'N/A'}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        ) : (
                            <div className="py-12 text-center text-gray-500 italic text-xs">
                                No labeled volumes ready. Please go to the Patient Labeling tab to review and assign labels to your candidates.
                            </div>
                        )}
                    </div>

                    {/* Bottom Approval Button Gate */}
                    <div className="flex flex-col items-end gap-2 pt-2 border-t border-[#1f2d40]">
                        <button
                            onClick={handleApproveCohort}
                            disabled={!canApprove || isApproving || cohortApproved}
                            title={cohortApproved ? 'Cohort is already approved and locked.' : !canApprove ? 'You must label at least one volume in the Patient Labeling tab before approving.' : 'Approve cohort and resume segmentation pipeline'}
                            className={`flex items-center gap-2 px-6 py-3 rounded-lg text-sm font-bold uppercase tracking-wider transition-all border shadow-xl ${
                                !canApprove
                                    ? 'bg-[#151a1f] text-gray-500 border-gray-700 cursor-not-allowed opacity-60'
                                    : cohortApproved
                                        ? 'bg-[#152e2e] text-[#5ce6b0] border-[#234d4d] cursor-not-allowed pointer-events-none select-none shadow-[0_0_20px_rgba(92,230,176,0.3)]'
                                        : 'bg-[#5acce6] hover:bg-[#43b2cc] text-black border-white/20 shadow-[0_0_20px_rgba(92,204,230,0.4)] cursor-pointer'
                            }`}
                        >
                            {cohortApproved ? (
                                <>
                                    <CheckCircleIcon size={18} /> Cohort Approved & Locked
                                </>
                            ) : isApproving ? (
                                <>
                                    <RefreshCwIcon size={18} className="animate-spin" /> Locking Cohort...
                                </>
                            ) : (
                                <>
                                    <LockIcon size={18} /> Approve & Lock Cohort
                                </>
                            )}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
