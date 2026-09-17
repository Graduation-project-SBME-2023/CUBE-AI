import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Users,
  Filter,
  Database,
  AlertTriangle,
  Check,
  Activity,
  Layers,
  Table,
  Tag,
  CheckCircle2,
  Save,
  ShieldCheck,
  Search,
  Info,
  RefreshCw,
  Lock,
  AlertCircle,
  Eye,
} from 'lucide-react';
import { useSnackbar } from '@ohif/ui';
import { getBackendApiBaseUrl, getAgentsApiBaseUrl } from '../../../utils/apiBase';
import websocketService from '../../../services/websocket';

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
const EyeIcon: any = Eye;

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
      <div className="relative flex h-full w-full items-center justify-center bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-[#1f2d40] via-[#0b1015] to-black">
        <Database
          size={24}
          className="text-[#3c5d80]"
        />
      </div>
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      {/* Frozen first-frame canvas */}
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute inset-0 z-10 h-full w-full object-cover"
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
        className="absolute inset-0 z-20 h-full w-full object-cover"
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

export default function DataCohortView({
  populationData,
  segmentationData,
  sessionId,
  isPlanApproved = false,
}: DataCohortViewProps) {
  const { show } = useSnackbar();

  // Active Subpage State
  const [subpage, setSubpage] = useState<'overview' | 'browser' | 'labeling' | 'approval'>(
    'overview'
  );

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
  const [isApproving, setIsApproving] = useState<boolean>(false);

  // Reset all local cohort state whenever the plan becomes freshly approved (new run)
  // or whenever the session changes — guarantees a clean slate.
  const prevIsPlanApprovedRef = useRef(isPlanApproved);
  useEffect(() => {
    const wasApproved = prevIsPlanApprovedRef.current;
    prevIsPlanApprovedRef.current = isPlanApproved;
    // Only reset when transitioning from not-approved → approved (new execution starting)
    if (!wasApproved && isPlanApproved) {
      setAccumulatedPop({
        count: null,
        uids: [],
        inclusion: [],
        exclusion: [],
        modality: 'Awaiting...',
        summary: '',
        matchRate: null,
      });
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
          next.inclusion = Array.isArray(populationData.inclusion_criteria)
            ? populationData.inclusion_criteria
            : [populationData.inclusion_criteria as any];
        }
        if (populationData.exclusion_criteria) {
          next.exclusion = Array.isArray(populationData.exclusion_criteria)
            ? populationData.exclusion_criteria
            : [populationData.exclusion_criteria as any];
        }
        if (populationData.modality) next.modality = populationData.modality;
      }
      if (
        populationData.tool === 'search_volumes_by_description' ||
        populationData.tool === 'validate_population_anatomy'
      ) {
        if (populationData.count !== undefined) next.count = populationData.count;
        if (populationData.uid_list) next.uids = populationData.uid_list;
      }
      if (populationData.outputs?.metrics) {
        const metrics = populationData.outputs.metrics;
        if (metrics.cohort_size) next.count = metrics.cohort_size;
        if (metrics.match_rate) next.matchRate = metrics.match_rate;
        if (metrics.inclusion_criteria) {
          next.inclusion = Array.isArray(metrics.inclusion_criteria)
            ? metrics.inclusion_criteria
            : [metrics.inclusion_criteria as any];
        }
        if (metrics.exclusion_criteria) {
          next.exclusion = Array.isArray(metrics.exclusion_criteria)
            ? metrics.exclusion_criteria
            : [metrics.exclusion_criteria as any];
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
      const res = await fetch(
        `${getAgentsApiBaseUrl()}/populations/cohort-state?session_id=${activeSessionId}`
      );
      if (res.ok) {
        const data = await res.json();
        if (data.labelling_mode) setLabellingMode(data.labelling_mode);
        if (data.cohort_approved !== undefined) setCohortApproved(data.cohort_approved);
        if (data.datasetlist && Array.isArray(data.datasetlist)) {
          setAccumulatedPop(prev => ({
            ...prev,
            uids: data.datasetlist,
            count: data.datasetlist.length,
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
            setAccumulatedPop(prev => ({
              ...prev,
              uids: cfg.datasetlist,
              count: cfg.datasetlist.length,
            }));
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

    const unsubscribeWs = websocketService.subscribe((event: any) => {
      if (
        event?.type === 'cohort_labels_output' ||
        event?.type === 'population_output' ||
        event?.type === 'generate_targets_output'
      ) {
        fetchCohortState();
      }
    });

    return () => {
      window.removeEventListener('labellingModeChanged', handleModeChange);
      unsubscribeWs();
    };
  }, [isPlanApproved, populationData, subpage, fetchCohortState]);

  // Fetch all Orthanc volumes when opening Data Browser — only after plan approval
  const fetchAllVolumes = async () => {
    if (!isPlanApproved) return;
    setIsLoadingVolumes(true);
    try {
      const res = await fetch(`${getBackendApiBaseUrl()}/volumes/?limit=100`);
      if (res.ok) {
        const vols: OrthancVolume[] = await res.json();
        setAllVolumes(vols);
      } else {
        console.error('Failed to fetch volumes from Orthanc');
      }
    } catch (err) {
      console.error('Error fetching Orthanc volumes:', err);
    } finally {
      setIsLoadingVolumes(false);
    }
  };

  useEffect(() => {
    if ((subpage === 'browser' || subpage === 'labeling') && isPlanApproved) {
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
        source_evidence:
          labellingMode === 'automatic'
            ? 'No clinical report match found. Pending user selection.'
            : 'Manual mode — awaiting doctor selection.',
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
        body: JSON.stringify({ session_id: activeSessionId, datasetlist: nextUids }),
      });
      if (!res.ok) {
        show({
          title: 'Error',
          message: 'Failed to update cohort list in backend.',
          type: 'error',
          duration: 4000,
        });
      }
    } catch (err) {
      console.error('Error updating cohort list:', err);
      show({
        title: 'Network Error',
        message: 'Failed to communicate with backend.',
        type: 'error',
        duration: 4000,
      });
    }
  };

  // Handle saving patient label edits from Subpage 3
  const handleUpdateLabelValue = (uid: string, value: string) => {
    setLabels(prev => {
      const exists = prev.some(l => l.patient_id === uid);
      if (exists) {
        return prev.map(l =>
          l.patient_id === uid
            ? { ...l, has_target_disease: value, confidence: 'manual override' }
            : l
        );
      } else {
        return [
          ...prev,
          {
            patient_id: uid,
            has_target_disease: value,
            confidence: 'manual override',
            requires_manual_review: false,
            source_evidence: 'Manually assigned in UI',
          },
        ];
      }
    });
    setCohortApproved(false);
  };

  // Persists whatever labels are currently on screen. Both the explicit
  // "Save & Apply Labels" button and Approve (which must never freeze a
  // cohort against stale backend data — pill clicks only update local
  // state, see handleUpdateLabelValue) call this.
  const persistLabels = async (activeSessionId: string): Promise<boolean> => {
    const res = await fetch(`${getAgentsApiBaseUrl()}/populations/save-labels`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: activeSessionId, labels: unifiedLabels }),
    });
    return res.ok;
  };

  const handleSaveLabels = async () => {
    const activeSessionId = sessionStorage.getItem('active_cube_session') || sessionId;
    if (!activeSessionId) {
      show({ title: 'Error', message: 'No active session found.', type: 'error', duration: 3000 });
      return;
    }

    setIsSavingLabels(true);
    try {
      const ok = await persistLabels(activeSessionId);
      if (ok) {
        show({
          title: 'Success',
          message: 'Patient labels saved successfully!',
          type: 'success',
          duration: 4000,
        });
        setCohortApproved(false);
        await fetchCohortState();
      } else {
        show({
          title: 'Error',
          message: 'Failed to save patient labels.',
          type: 'error',
          duration: 4000,
        });
      }
    } catch (err) {
      console.error('Error saving labels:', err);
      show({
        title: 'Error',
        message: 'Network error while saving labels.',
        type: 'error',
        duration: 4000,
      });
    } finally {
      setIsSavingLabels(false);
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
      // The label pills only update local state (see handleUpdateLabelValue) —
      // without this, approving right after labeling (without a separate
      // "Save & Apply Labels" click) freezes the cohort against whatever was
      // last persisted, which can still show 0 labeled volumes server-side
      // and reject the approval the doctor just tried to make.
      const saved = await persistLabels(activeSessionId);
      if (!saved) {
        show({
          title: 'Cannot Approve Cohort',
          message: 'Could not save the current labels; approval was not attempted.',
          type: 'error',
          duration: 5000,
        });
        return;
      }

      const res = await fetch(`${getAgentsApiBaseUrl()}/populations/approve-cohort`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: activeSessionId }),
      });
      const body = await res.json();
      if (res.ok) {
        setCohortApproved(true);
        show({
          title: 'Cohort Approved!',
          message: `Successfully locked ${body.labeled_count} labeled volume(s).`,
          type: 'success',
          duration: 6000,
        });
        await fetchCohortState();
      } else {
        show({
          title: 'Cannot Approve Cohort',
          message: body.detail || 'Validation failed during cohort approval.',
          type: 'error',
          duration: 5000,
        });
      }
    } catch (err) {
      console.error('Error approving cohort:', err);
      show({
        title: 'Network Error',
        message: 'Could not contact approval backend.',
        type: 'error',
        duration: 4000,
      });
    } finally {
      setIsApproving(false);
    }
  };

  // Filtered lists for Subpage 2 (Browser) and Subpage 4 (Final Cohort)
  const filteredBrowserVolumes = useMemo(() => {
    return allVolumes.filter(vol => {
      const matchesSearch =
        !browserSearchTerm ||
        vol.volume_series_instance_uid.toLowerCase().includes(browserSearchTerm.toLowerCase()) ||
        (vol.series_description || '').toLowerCase().includes(browserSearchTerm.toLowerCase()) ||
        (vol.body_part_examined || '').toLowerCase().includes(browserSearchTerm.toLowerCase());
      const matchesModality =
        selectedModalityFilter === 'all' ||
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
      <div className="flex h-full w-full select-none flex-col items-center justify-center gap-6 bg-[#000000] text-white">
        {/* Spinner */}
        <div className="relative flex items-center justify-center">
          <div className="absolute h-20 w-20 rounded-full border-2 border-[#1a2a3a] opacity-40" />
          <div
            className="h-20 w-20 rounded-full border-2 border-transparent"
            style={{
              borderTopColor: '#5acce6',
              borderRightColor: '#3c5d8040',
              animation: 'spin 1.1s linear infinite',
            }}
          />
          <Database
            size={22}
            className="absolute text-[#3c6d8a]"
          />
        </div>

        {/* Label */}
        <div className="flex flex-col items-center gap-2">
          <p className="text-[15px] font-semibold tracking-wide text-[#91b9cd]">
            Awaiting Plan Approval
          </p>
          <p className="max-w-[280px] text-center text-xs leading-relaxed text-[#4a6a80]">
            The Data &amp; Population tab will be available once you review and approve the
            generated study plan.
          </p>
        </div>

        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col gap-6 overflow-hidden bg-[#000000] p-6 text-white">
      {/* ── TOP SUB-NAVIGATION TABS ── */}
      <div className="flex shrink-0 items-center justify-between border-b border-[#1f2d40] px-1 pb-2">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSubpage('overview')}
            className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-bold uppercase tracking-wider transition-all ${
              subpage === 'overview'
                ? 'border-[#3c5d80] bg-[#152433] text-[#5acce6] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                : 'border-[#1f2d40] bg-[#0b1015] text-[#91b9cd] hover:border-[#3c5d80] hover:text-white'
            }`}
          >
            <DatabaseIcon size={15} /> Overview
            <span className="ml-1 rounded border border-[#1f2d40] bg-[#05080a] px-2 py-0.5 text-[10px] text-gray-400">
              {accumulatedPop.count ?? 0}
            </span>
          </button>

          <button
            onClick={() => setSubpage('browser')}
            className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-bold uppercase tracking-wider transition-all ${
              subpage === 'browser'
                ? 'border-[#3c5d80] bg-[#152433] text-[#5acce6] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                : 'border-[#1f2d40] bg-[#0b1015] text-[#91b9cd] hover:border-[#3c5d80] hover:text-white'
            }`}
          >
            <TableIcon size={15} /> Data Browser (Table)
          </button>

          <button
            onClick={() => setSubpage('labeling')}
            className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-bold uppercase tracking-wider transition-all ${
              subpage === 'labeling'
                ? 'border-[#3c5d80] bg-[#152433] text-[#5acce6] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                : 'border-[#1f2d40] bg-[#0b1015] text-[#91b9cd] hover:border-[#3c5d80] hover:text-white'
            }`}
          >
            <TagIcon size={15} /> Patient Labeling
            <span
              className="ml-1 rounded border border-[#1f2d40] bg-[#05080a] px-2 py-0.5 text-[10px]"
              style={{
                color:
                  finalLabeledCohort.length === accumulatedPop.uids.length &&
                  accumulatedPop.uids.length > 0
                    ? '#6fe5b8'
                    : '#ffcf6a',
              }}
            >
              {finalLabeledCohort.length} / {accumulatedPop.uids.length}
            </span>
          </button>

          <button
            onClick={() => setSubpage('approval')}
            className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-bold uppercase tracking-wider transition-all ${
              subpage === 'approval'
                ? 'border-[#3c5d80] bg-[#152433] text-[#5acce6] shadow-[0_0_15px_rgba(92,204,230,0.15)]'
                : 'border-[#1f2d40] bg-[#0b1015] text-[#91b9cd] hover:border-[#3c5d80] hover:text-white'
            }`}
          >
            <ShieldCheckIcon size={15} /> Cohort Approval Gate
            {cohortApproved ? (
              <span className="ml-1 rounded border border-[#234d4d] bg-[#152e2e] px-2 py-0.5 text-[10px] font-bold text-[#5ce6b0]">
                APPROVED
              </span>
            ) : (
              <span className="ml-1 rounded border border-[#4d4223] bg-[#2e2615] px-2 py-0.5 text-[10px] font-bold text-[#ffcf6a]">
                PENDING
              </span>
            )}
          </button>
        </div>

        <div className="flex items-center gap-3 font-mono text-xs">
          <span className="text-gray-400">
            Labelling Mode: <strong className="uppercase text-[#5acce6]">{labellingMode}</strong>
          </span>
          <button
            onClick={() => fetchCohortState()}
            title="Refresh Cohort State"
            className="rounded border border-[#1f2d40] bg-[#0b1015] p-1.5 text-[#91b9cd] transition-colors hover:bg-[#1f2d40]"
          >
            <RefreshCwIcon size={14} />
          </button>
        </div>
      </div>

      {/* ── SUBPAGE 1: OVERVIEW ── */}
      {subpage === 'overview' && (
        <div className="flex min-h-0 w-full flex-1 gap-6 overflow-hidden">
          {/* LEFT: Criteria & Summaries */}
          <div
            className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto pb-4 pr-1
                        [&::-webkit-scrollbar]:w-2
                        [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                        [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                        hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
          >
            <div className="flex flex-col gap-4 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
              <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[#5acce6]">
                <FilterIcon size={14} /> Study Criteria
              </h3>

              {accumulatedPop.modality && accumulatedPop.modality !== 'Awaiting...' && (
                <div className="inline-block w-max rounded border border-[#3c5d80] bg-[#3c5d80]/20 px-2 py-1 text-xs text-[#91b9cd]">
                  Target: <span className="font-bold text-white">{accumulatedPop.modality}</span>
                </div>
              )}

              {/* Inclusion */}
              <div className="space-y-2">
                <span className="inline-block rounded border border-[#6fe5b8]/20 bg-[#6fe5b8]/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#6fe5b8]">
                  Inclusion
                </span>
                <div
                  className="max-h-[200px] min-h-[60px] space-y-1 overflow-y-auto rounded border border-[#3c5d80]/30 bg-[#0b1015] p-3 text-xs text-gray-300
                                    [&::-webkit-scrollbar]:w-2
                                    [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                                    [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                                    hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
                >
                  {accumulatedPop.inclusion.length > 0 ? (
                    accumulatedPop.inclusion.map((c, i) => (
                      <div
                        key={i}
                        className="flex items-start gap-2"
                      >
                        <CheckIcon
                          size={12}
                          className="mt-0.5 shrink-0 text-[#6fe5b8]"
                        />
                        <span>{c}</span>
                      </div>
                    ))
                  ) : (
                    <div className="italic text-gray-600">
                      Waiting for agent to define criteria...
                    </div>
                  )}
                </div>
              </div>

              {/* Exclusion */}
              <div className="space-y-2">
                <span className="inline-block rounded border border-[#ff6b6b]/20 bg-[#ff6b6b]/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#ff6b6b]">
                  Exclusion
                </span>
                <div
                  className="max-h-[200px] min-h-[60px] space-y-1 overflow-y-auto rounded border border-[#3c5d80]/30 bg-[#0b1015] p-3 text-xs text-gray-300
                                    [&::-webkit-scrollbar]:w-2
                                    [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                                    [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                                    hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
                >
                  {accumulatedPop.exclusion.length > 0 ? (
                    accumulatedPop.exclusion.map((c, i) => (
                      <div
                        key={i}
                        className="flex items-start gap-2"
                      >
                        <AlertTriangleIcon
                          size={12}
                          className="mt-0.5 shrink-0 text-[#ff6b6b]"
                        />
                        <span>{c}</span>
                      </div>
                    ))
                  ) : (
                    <div className="italic text-gray-600">Waiting for exclusion logic...</div>
                  )}
                </div>
              </div>
            </div>

            {/* Cohort Summary */}
            <div className="shrink-0 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">
                Cohort Summary
              </h3>
              <div className="mb-1 text-3xl font-bold text-white transition-all">
                {accumulatedPop.count ?? '—'}{' '}
                <span className="text-sm font-normal text-gray-500">patients</span>
              </div>
              {accumulatedPop.matchRate !== null && (
                <>
                  <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[#0b1015]">
                    <div
                      className="h-full bg-[#5acce6] transition-all duration-1000"
                      style={{ width: `${accumulatedPop.matchRate}%` }}
                    />
                  </div>
                  <div className="mt-1 text-right text-[10px] text-gray-500">
                    {accumulatedPop.matchRate}% Match Rate
                  </div>
                </>
              )}
              {accumulatedPop.summary && (
                <p className="mt-3 rounded border border-[#1f2d40] bg-[#0b1015] p-2 text-xs leading-relaxed text-[#5acce6]">
                  {accumulatedPop.summary}
                </p>
              )}
            </div>

            {/* Segmentation Summary */}
            {hasSegData && (
              <div className="shrink-0 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
                <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[#5acce6]">
                  <LayersIcon size={14} /> Segmentation
                </h3>
                {segMethod && (
                  <div className="mb-1 text-xs text-gray-300">
                    <span className="text-gray-500">Method:</span> {segMethod}
                  </div>
                )}
                {volumesSegmented !== null && (
                  <div className="text-2xl font-bold text-white">
                    {volumesSegmented}{' '}
                    <span className="text-sm font-normal text-gray-500">volumes</span>
                  </div>
                )}
                {roiDesc && <div className="mt-1 text-xs text-gray-400">{roiDesc}</div>}
              </div>
            )}
          </div>

          {/* RIGHT: Selected Data Grid */}
          <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-[#3c5d80] bg-[#151a1f] p-6">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-lg font-bold text-white">
                  <DatabaseIcon
                    size={18}
                    className="text-[#5acce6]"
                  />{' '}
                  Data Acquisition Preview
                </h2>
                <p className="mt-1 text-xs text-gray-400">
                  {hasPopData
                    ? `Agent Activity: Processing ${populationData?.tool?.replace(/_/g, ' ') || 'finalizing'}...`
                    : 'Waiting for Population Agent to initiate DB search...'}
                </p>
              </div>
              <div className="flex items-center gap-2 font-mono text-xs">
                {cohortApproved ? (
                  <span className="flex items-center gap-2 rounded border border-[#234d4d] bg-[#152e2e] px-3 py-1.5 text-[#5ce6b0] shadow-[0_0_10px_rgba(92,230,176,0.2)]">
                    <ActivityIcon
                      size={12}
                      className="animate-pulse"
                    />{' '}
                    Cohort Secured & Approved
                  </span>
                ) : (
                  <span className="flex items-center gap-2 rounded border border-[#4d4223] bg-[#2e2615] px-3 py-1.5 text-[#ffcf6a] shadow-[0_0_10px_rgba(255,207,106,0.15)]">
                    <AlertTriangleIcon size={12} /> Pending Cohort Approval
                  </span>
                )}
              </div>
            </div>

            <div
              className="grid grid-cols-2 gap-3 overflow-y-auto pr-2 pb-4 md:grid-cols-3 lg:grid-cols-4
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#151a1f]
                            [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
            >
              {accumulatedPop.uids.length > 0
                ? accumulatedPop.uids.map(uid => {
                    const isLabeled = finalLabeledCohort.some(l => l.patient_id === uid);
                    return (
                      <div
                        key={uid}
                        className="group relative block w-full overflow-hidden rounded border border-[#3c5d80]/50 bg-[#05080a] transition-all hover:border-[#5acce6]"
                        style={{ paddingTop: '100%' }}
                        onMouseEnter={() => setHoveredUid(uid)}
                        onMouseLeave={() => setHoveredUid(null)}
                      >
                        <div className="absolute inset-0 flex flex-col">
                          <div className="relative min-h-0 w-full flex-1 overflow-hidden bg-black">
                            <PausableGif
                              uid={uid}
                              isHovered={hoveredUid === uid}
                            />
                            <div className="pointer-events-none absolute top-2 right-2 z-30">
                              {isLabeled ? (
                                <div className="rounded border border-[#5ce6b0]/30 bg-[#152e2e]/90 px-1.5 py-0.5 text-[8px] font-bold uppercase text-[#5ce6b0] shadow-lg backdrop-blur">
                                  READY
                                </div>
                              ) : (
                                <div className="rounded border border-[#ffcf6a]/30 bg-[#2e2615]/90 px-1.5 py-0.5 text-[8px] font-bold uppercase text-[#ffcf6a] shadow-lg backdrop-blur">
                                  PENDING LABEL
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="relative z-20 flex h-7 w-full shrink-0 items-center justify-center border-t border-[#1f2d40] bg-[#0b1015] px-2 transition-colors group-hover:bg-[#151a1f]">
                            <span
                              className="w-full truncate text-center font-mono text-[10px] text-[#91b9cd]"
                              title={uid}
                            >
                              ...{uid.slice(-12)}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })
                : Array.from({ length: 12 }).map((_, i) => (
                    <div
                      key={`skeleton-${i}`}
                      className="relative block w-full animate-pulse overflow-hidden rounded border border-[#1f2d40] bg-[#0b1015]"
                      style={{ paddingTop: '100%' }}
                    >
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="font-mono text-[10px] text-gray-700">Awaiting Data</span>
                      </div>
                    </div>
                  ))}
            </div>
          </div>
        </div>
      )}

      {/* ── SUBPAGE 2: DATA BROWSER (TABLE VIEW) ── */}
      {subpage === 'browser' && (
        <div className="flex min-h-0 flex-1 flex-col gap-4 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-6">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#1f2d40] pb-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-bold text-white">
                <TableIcon
                  size={18}
                  className="text-[#5acce6]"
                />{' '}
                PACS Data Browser
              </h2>
              <p className="mt-1 text-xs text-gray-400">
                Review and toggle inclusion for all imaging datasets available in Orthanc PACS.
              </p>
            </div>

            <div className="flex items-center gap-3">
              {/* Search filter */}
              <div className="relative w-64">
                <SearchIcon
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500"
                />
                <input
                  type="text"
                  value={browserSearchTerm}
                  onChange={e => setBrowserSearchTerm(e.target.value)}
                  placeholder="Search organ, description, UID..."
                  className="w-full rounded-lg border border-[#1f2d40] bg-[#0b1015] py-1.5 pl-8 pr-3 text-xs text-white focus:border-[#5acce6] focus:outline-none"
                />
              </div>

              {/* Modality filter */}
              <select
                value={selectedModalityFilter}
                onChange={e => setSelectedModalityFilter(e.target.value)}
                className="rounded-lg border border-[#1f2d40] bg-[#0b1015] px-3 py-1.5 text-xs text-[#91b9cd] focus:border-[#5acce6] focus:outline-none"
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
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-xs text-gray-500">
              <RefreshCwIcon
                size={24}
                className="animate-spin text-[#5acce6]"
              />
              Fetching available imaging data from Orthanc...
            </div>
          ) : (
            <div
              className="flex-1 overflow-y-auto rounded-lg border border-[#1f2d40] bg-[#0b1015]
                            [&::-webkit-scrollbar]:w-2
                            [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                            [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                            hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
            >
              <table className="w-full border-collapse text-left">
                <thead className="sticky top-0 z-20 border-b border-[#3c5d80] bg-[#152433] text-[11px] font-bold uppercase tracking-wider text-[#5acce6]">
                  <tr>
                    <th className="w-12 py-3 px-4 text-center">Include</th>
                    <th
                      className="w-12 py-3 px-2 text-center"
                      title="View in DICOM Viewer"
                    >
                      View
                    </th>
                    <th className="w-16 py-3 px-4 text-center">Order</th>
                    <th className="py-3 px-4">Organ / Body Part</th>
                    <th className="py-3 px-4">Modality</th>
                    <th className="py-3 px-4">Series Description</th>
                    <th className="w-24 py-3 px-4 text-right">Slices</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1f2d40] text-xs">
                  {filteredBrowserVolumes.length > 0 ? (
                    filteredBrowserVolumes.map((vol, index) => {
                      const isChecked = accumulatedPop.uids.includes(
                        vol.volume_series_instance_uid
                      );
                      return (
                        <tr
                          key={vol.volume_series_instance_uid || index}
                          className={`cursor-pointer transition-colors hover:bg-[#151a1f]/80 ${isChecked ? 'bg-[#152433]/30' : ''}`}
                          onClick={() =>
                            handleToggleVolumeInclusion(vol.volume_series_instance_uid)
                          }
                        >
                          <td
                            className="py-3 px-4 text-center"
                            onClick={e => e.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() =>
                                handleToggleVolumeInclusion(vol.volume_series_instance_uid)
                              }
                              className="h-4 w-4 cursor-pointer rounded border-[#3c5d80] bg-[#0b1015] text-[#5acce6] accent-[#5acce6] focus:ring-0"
                            />
                          </td>
                          <td
                            className="py-3 px-2 text-center"
                            onClick={e => e.stopPropagation()}
                          >
                            <button
                              type="button"
                              onClick={() => {
                                const studyUid = vol.study_uid || '';
                                window.open(
                                  `/viewer?StudyInstanceUIDs=${studyUid}&seriesInstanceUID=${vol.volume_series_instance_uid}`,
                                  '_blank',
                                  'noopener,noreferrer'
                                );
                              }}
                              title="Open volume in OHIF 3D DICOM Viewer (New Tab)"
                              className="inline-flex items-center justify-center rounded border border-[#3c5d80] bg-[#152433] p-1.5 text-[#5acce6] shadow transition-colors hover:border-[#5acce6] hover:bg-[#5acce6]/20"
                            >
                              <EyeIcon size={14} />
                            </button>
                          </td>
                          <td className="py-3 px-4 text-center font-mono font-bold text-[#5ce6b0]">
                            #
                            {allVolumes.findIndex(
                              v => v.volume_series_instance_uid === vol.volume_series_instance_uid
                            ) + 1}
                          </td>
                          <td className="py-3 px-4 font-semibold text-white">
                            {vol.body_part_examined || (
                              <span className="italic text-gray-600">Unspecified</span>
                            )}
                          </td>
                          <td className="py-3 px-4">
                            <span className="rounded bg-[#1f2d40] px-2 py-0.5 font-mono text-[10px] text-[#5acce6]">
                              {vol.modality || '—'}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-gray-300">
                            {vol.series_description || (
                              <span className="italic text-gray-600">No series description</span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-right font-mono text-gray-400">
                            {vol.num_slices ?? '—'}
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td
                        colSpan={7}
                        className="py-12 text-center italic text-gray-500"
                      >
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
        <div className="flex min-h-0 flex-1 flex-col gap-4 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-6">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#1f2d40] pb-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-bold text-white">
                <TagIcon
                  size={18}
                  className="text-[#5acce6]"
                />{' '}
                Interactive Patient Labeling
              </h2>
              <p className="mt-1 text-xs text-gray-400">
                Assign or review Target Disease status (Positive / Negative). All edits persist
                directly in the backend session without external CSV uploads.
              </p>
            </div>

            <button
              onClick={handleSaveLabels}
              disabled={isSavingLabels || cohortApproved}
              className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-bold uppercase tracking-wider shadow-lg transition-all ${
                cohortApproved
                  ? 'pointer-events-none cursor-not-allowed select-none border-gray-700 bg-[#152e2e]/30 text-gray-600 opacity-50 shadow-none'
                  : isSavingLabels
                    ? 'cursor-not-allowed border-gray-700 bg-[#152e2e]/50 text-gray-500'
                    : 'border-[#234d4d] bg-[#152e2e] text-[#5ce6b0] shadow-[0_0_15px_rgba(92,230,176,0.2)] hover:bg-[#1f4242]'
              }`}
            >
              <SaveIcon size={14} />
              {isSavingLabels ? 'Saving...' : 'Save & Apply Labels'}
            </button>
          </div>

          <div
            className="flex-1 overflow-y-auto rounded-lg border border-[#1f2d40] bg-[#0b1015]
                        [&::-webkit-scrollbar]:w-2
                        [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                        [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                        hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
          >
            <table className="w-full border-collapse text-left">
              <thead className="sticky top-0 z-20 border-b border-[#3c5d80] bg-[#152433] text-[11px] font-bold uppercase tracking-wider text-[#5acce6]">
                <tr>
                  <th className="w-16 py-3 px-4 text-center">Order</th>
                  <th
                    className="w-12 py-3 px-2 text-center"
                    title="View in DICOM Viewer"
                  >
                    View
                  </th>
                  <th className="w-52 py-3 px-4">Target Disease Label</th>
                  <th className="w-40 py-3 px-4">Confidence</th>
                  <th className="py-3 px-4">Clinical Evidence & Rationale</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1f2d40] text-xs">
                {unifiedLabels.length > 0 ? (
                  unifiedLabels.map(row => {
                    const htd = row.has_target_disease?.toString().trim().toLowerCase();
                    const isLowConf =
                      row.confidence?.toLowerCase() === 'low' ||
                      row.confidence?.toLowerCase() === 'unlabeled';

                    return (
                      <tr
                        key={row.patient_id}
                        className="transition-colors hover:bg-[#151a1f]/70"
                      >
                        <td className="py-3 px-4 text-center font-mono font-bold text-[#5ce6b0]">
                          #
                          {allVolumes.findIndex(
                            v => v.volume_series_instance_uid === row.patient_id
                          ) + 1}
                        </td>
                        <td
                          className="py-3 px-2 text-center"
                          onClick={e => e.stopPropagation()}
                        >
                          {(() => {
                            const match = allVolumes.find(
                              v => v.volume_series_instance_uid === row.patient_id
                            );
                            return (
                              <button
                                type="button"
                                onClick={() => {
                                  const studyUid = match?.study_uid || '';
                                  window.open(
                                    `/viewer?StudyInstanceUIDs=${studyUid}&seriesInstanceUID=${row.patient_id}`,
                                    '_blank',
                                    'noopener,noreferrer'
                                  );
                                }}
                                title="Open volume in OHIF 3D DICOM Viewer (New Tab)"
                                className="inline-flex items-center justify-center rounded border border-[#3c5d80] bg-[#152433] p-1.5 text-[#5acce6] shadow transition-colors hover:border-[#5acce6] hover:bg-[#5acce6]/20"
                              >
                                <EyeIcon size={14} />
                              </button>
                            );
                          })()}
                        </td>

                        {/* Label selection pills */}
                        <td className="py-3 px-4">
                          <div className="flex w-max items-center gap-1 rounded-lg border border-[#1f2d40] bg-[#05080a] p-1">
                            <button
                              type="button"
                              onClick={() => handleUpdateLabelValue(row.patient_id, 'true')}
                              className={`rounded px-3 py-1 text-[11px] font-bold transition-colors ${
                                htd === 'true' || htd === '1'
                                  ? 'border border-[#234d4d] bg-[#152e2e] text-[#5ce6b0]'
                                  : 'text-gray-400 hover:text-white'
                              }`}
                            >
                              Positive (+1)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleUpdateLabelValue(row.patient_id, 'false')}
                              className={`rounded px-3 py-1 text-[11px] font-bold transition-colors ${
                                htd === 'false' || htd === '0'
                                  ? 'border border-[#522424] bg-[#331818] text-[#ff6b6b]'
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
                            <span className="rounded border border-[#234d4d] bg-[#152e2e] px-2 py-0.5 font-mono text-[10px] uppercase text-[#5ce6b0]">
                              High
                            </span>
                          )}
                          {row.confidence?.toLowerCase() === 'medium' && (
                            <span className="rounded border border-[#4d4223] bg-[#2e2615] px-2 py-0.5 font-mono text-[10px] uppercase text-[#ffcf6a]">
                              Medium
                            </span>
                          )}
                          {isLowConf && (
                            <span className="inline-flex animate-pulse items-center gap-1 rounded border border-[#522424] bg-[#331818] px-2 py-0.5 font-mono text-[10px] font-bold uppercase text-[#ff6b6b]">
                              <AlertCircleIcon size={11} /> {row.confidence || 'Low'}
                            </span>
                          )}
                          {(!row.confidence ||
                            (row.confidence.toLowerCase() !== 'high' &&
                              row.confidence.toLowerCase() !== 'medium' &&
                              !isLowConf)) && (
                            <span className="font-mono text-[10px] text-gray-500">
                              {row.confidence || '—'}
                            </span>
                          )}
                        </td>

                        {/* Evidence / Orphan status */}
                        <td className="py-3 px-4 leading-relaxed text-gray-300">
                          {row.source_evidence ? (
                            <span>{row.source_evidence}</span>
                          ) : (
                            <span className="italic text-gray-600">
                              No automated evidence recorded.
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td
                      colSpan={5}
                      className="py-12 text-center italic text-gray-500"
                    >
                      No study candidates available to label. Please wait for the Population Agent
                      or add volumes via the Data Browser.
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
        <div className="flex min-h-0 flex-1 flex-col gap-6 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-6">
          {/* Summary statistics bar */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-4">
              <div className="text-[11px] font-bold uppercase text-gray-400">Total Discovered</div>
              <div className="mt-1 text-2xl font-bold text-white">{accumulatedPop.uids.length}</div>
            </div>
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-4">
              <div className="text-[11px] font-bold uppercase text-[#6fe5b8]">
                Valid Labeled Cohort
              </div>
              <div className="mt-1 text-2xl font-bold text-[#5ce6b0]">
                {finalLabeledCohort.length}
              </div>
            </div>
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-4">
              <div className="text-[11px] font-bold uppercase text-[#5acce6]">
                Positive Targets (+1)
              </div>
              <div className="mt-1 text-2xl font-bold text-[#5acce6]">{posCount}</div>
            </div>
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-4">
              <div className="text-[11px] font-bold uppercase text-[#ff6b6b]">
                Negative Targets (0)
              </div>
              <div className="mt-1 text-2xl font-bold text-[#ff6b6b]">{negCount}</div>
            </div>
          </div>

          {/* Final Approved Grid / List Preview */}
          <div
            className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-[#1f2d40] bg-[#0b1015] p-4
                        [&::-webkit-scrollbar]:w-2
                        [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015]
                        [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80]
                        hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]"
          >
            <h3 className="mb-4 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">
              <CheckCircleIcon
                size={14}
                className="text-[#5ce6b0]"
              />{' '}
              Final Approved Volume Candidates ({finalLabeledCohort.length})
            </h3>

            {finalLabeledCohort.length > 0 ? (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
                {finalLabeledCohort.map(row => {
                  const isPos = row.has_target_disease?.toString().trim().toLowerCase() === 'true';
                  return (
                    <div
                      key={row.patient_id}
                      className="flex flex-col items-center justify-between rounded-lg border border-[#3c5d80]/40 bg-[#151a1f] p-2 text-center"
                    >
                      <span
                        className="w-full truncate font-mono text-[11px] text-gray-300"
                        title={row.patient_id}
                      >
                        ...{row.patient_id.slice(-12)}
                      </span>
                      <div className="my-2">
                        {isPos ? (
                          <span className="rounded border border-[#234d4d] bg-[#152e2e] px-2 py-0.5 text-[10px] font-bold text-[#5ce6b0]">
                            POSITIVE (+1)
                          </span>
                        ) : (
                          <span className="rounded border border-[#522424] bg-[#331818] px-2 py-0.5 text-[10px] font-bold text-[#ff6b6b]">
                            NEGATIVE (0)
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[9px] text-gray-500">
                        Conf: {row.confidence || 'N/A'}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="py-12 text-center text-xs italic text-gray-500">
                No labeled volumes ready. Please go to the Patient Labeling tab to review and assign
                labels to your candidates.
              </div>
            )}
          </div>

          {/* Bottom Approval Button Gate */}
          <div className="flex flex-col items-end gap-2 border-t border-[#1f2d40] pt-2">
            <button
              onClick={handleApproveCohort}
              disabled={!canApprove || isApproving || cohortApproved}
              title={
                cohortApproved
                  ? 'Cohort is already approved and locked.'
                  : !canApprove
                    ? 'You must label at least one volume in the Patient Labeling tab before approving.'
                    : 'Approve cohort and resume segmentation pipeline'
              }
              className={`flex items-center gap-2 rounded-lg border px-6 py-3 text-sm font-bold uppercase tracking-wider shadow-xl transition-all ${
                !canApprove
                  ? 'cursor-not-allowed border-gray-700 bg-[#151a1f] text-gray-500 opacity-60'
                  : cohortApproved
                    ? 'pointer-events-none cursor-not-allowed select-none border-transparent bg-[#1f2d40] text-[#3c5d80] shadow-none'
                    : 'cursor-pointer border-white/20 bg-[#5acce6] text-black shadow-[0_0_20px_rgba(92,204,230,0.4)] hover:bg-[#43b2cc]'
              }`}
            >
              {cohortApproved ? (
                <>
                  <CheckCircleIcon size={18} /> Cohort Approved & Locked
                </>
              ) : isApproving ? (
                <>
                  <RefreshCwIcon
                    size={18}
                    className="animate-spin"
                  />{' '}
                  Locking Cohort...
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
