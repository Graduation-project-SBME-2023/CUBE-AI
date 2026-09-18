import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  UploadCloud,
  FileText,
  FileStack,
  CheckCircle2,
  XCircle,
  Loader2,
  Trash2,
  ChevronDown,
  AlertTriangle,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  fetchIngestJob,
  isVolumeFile,
  uploadDicomFile,
  uploadVolumeFile,
  UploadError,
  VOLUME_EXTENSIONS,
  type IngestJob,
  type IngestModality,
} from '../utils/uploadMedicalFile';
import { getAgentsApiBaseUrl } from '../utils/apiBase';

// Icon aliases for lucide-react to satisfy JSX typing in this project.
const ArrowLeftIcon: any = ArrowLeft;
const UploadCloudIcon: any = UploadCloud;
const FileTextIcon: any = FileText;
const FileStackIcon: any = FileStack;
const CheckCircle2Icon: any = CheckCircle2;
const XCircleIcon: any = XCircle;
const Loader2Icon: any = Loader2;
const Trash2Icon: any = Trash2;
const ChevronDownIcon: any = ChevronDown;
const AlertTriangleIcon: any = AlertTriangle;

type RowStatus =
  | 'pending'
  | 'uploading'
  | 'queued'
  | 'converting'
  | 'uploading-slices'
  | 'verifying'
  | 'syncing'
  | 'completed'
  | 'failed';

interface QueueRow {
  id: string;
  file: File;
  kind: 'dicom' | 'volume';
  status: RowStatus;
  error?: string;
  jobId?: string;
  job?: IngestJob;
}

const MODALITIES: { value: IngestModality; label: string; hint: string }[] = [
  { value: 'CT', label: 'CT', hint: 'Hounsfield-unit CT volume' },
  { value: 'MR', label: 'MR', hint: 'Magnetic resonance volume' },
  { value: 'PT', label: 'PT', hint: 'PET volume' },
  { value: 'OT', label: 'Other', hint: 'Unknown scale — hidden from CT/MR search' },
];

const POLL_INTERVAL_MS = 1500;

const STATUS_META: Record<RowStatus, { label: string; tone: 'muted' | 'active' | 'ok' | 'error' }> = {
  pending: { label: 'Queued', tone: 'muted' },
  uploading: { label: 'Uploading', tone: 'active' },
  queued: { label: 'Queued', tone: 'muted' },
  converting: { label: 'Converting', tone: 'active' },
  'uploading-slices': { label: 'Uploading to Orthanc', tone: 'active' },
  verifying: { label: 'Verifying', tone: 'active' },
  syncing: { label: 'Registering', tone: 'active' },
  completed: { label: 'Stored', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
};

function jobStatusToRowStatus(status?: string): RowStatus {
  switch (status) {
    case 'queued':
      return 'queued';
    case 'converting':
      return 'converting';
    case 'uploading':
      return 'uploading-slices';
    case 'verifying':
      return 'verifying';
    case 'syncing':
      return 'syncing';
    case 'completed':
      return 'completed';
    case 'failed':
      return 'failed';
    default:
      return 'queued';
  }
}

function makeId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function fileKindLabel(filename: string): string {
  const lower = filename.toLowerCase();
  const match = VOLUME_EXTENSIONS.find(ext => lower.endsWith(ext));
  if (match) {
    return match.replace('.', '').toUpperCase();
  }
  return 'DICOM';
}

function rowDetail(row: QueueRow): string | null {
  if (row.kind === 'dicom') {
    return row.status === 'completed' ? 'Stored in Orthanc' : null;
  }
  const job = row.job;
  if (!job) return null;

  if (row.status === 'uploading-slices' && job.slice_count) {
    return `${job.uploaded_count ?? 0} / ${job.slice_count} slices`;
  }
  if (row.status === 'completed') {
    const verified = job.verification?.passed;
    const uid = job.series_instance_uid ? job.series_instance_uid.slice(-10) : '';
    return `${verified ? 'Verified' : 'Stored'} · series …${uid}`;
  }
  if (row.status === 'failed') {
    return job.error || 'Conversion failed';
  }
  return null;
}

export default function UploadStudies() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [modality, setModality] = useState<IngestModality>('CT');
  const [dragActive, setDragActive] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [studyUid, setStudyUid] = useState('');
  const [patientId, setPatientId] = useState('');
  const [patientName, setPatientName] = useState('');
  const [maxUploadBytes, setMaxUploadBytes] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const rowsRef = useRef<QueueRow[]>([]);
  rowsRef.current = rows;

  useEffect(() => {
    let cancelled = false;
    fetch(`${getAgentsApiBaseUrl()}/ingest/formats`)
      .then(response => (response.ok ? response.json() : null))
      .then(body => {
        if (!cancelled && body?.max_upload_bytes) {
          setMaxUploadBytes(body.max_upload_bytes);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // A single interval scans every row with an active background job instead
  // of one timer per file — the queue is small, and this keeps cleanup to one
  // place.
  useEffect(() => {
    const interval = setInterval(async () => {
      const pending = rowsRef.current.filter(
        row => row.jobId && row.status !== 'completed' && row.status !== 'failed'
      );
      if (pending.length === 0) return;

      const updates = await Promise.all(
        pending.map(async row => {
          try {
            const job = await fetchIngestJob(row.jobId as string);
            return { id: row.id, job };
          } catch (error) {
            return { id: row.id, job: null as IngestJob | null };
          }
        })
      );

      setRows(previous =>
        previous.map(row => {
          const update = updates.find(entry => entry.id === row.id);
          if (!update || !update.job) return row;
          return { ...row, job: update.job, status: jobStatusToRowStatus(update.job.status) };
        })
      );
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const startUpload = useCallback(
    async (row: QueueRow) => {
      try {
        if (row.kind === 'dicom') {
          setRows(previous =>
            previous.map(item => (item.id === row.id ? { ...item, status: 'uploading' } : item))
          );
          await uploadDicomFile(row.file);
          setRows(previous =>
            previous.map(item => (item.id === row.id ? { ...item, status: 'completed' } : item))
          );
          return;
        }

        setRows(previous =>
          previous.map(item => (item.id === row.id ? { ...item, status: 'queued' } : item))
        );
        const result = await uploadVolumeFile(row.file, {
          modality,
          studyUid: studyUid.trim() || undefined,
          patientId: patientId.trim() || undefined,
          patientName: patientName.trim() || undefined,
        });
        setRows(previous =>
          previous.map(item =>
            item.id === row.id
              ? { ...item, jobId: result.jobId, status: jobStatusToRowStatus(result.status) }
              : item
          )
        );
      } catch (error) {
        const message = error instanceof UploadError ? error.message : 'Upload failed';
        setRows(previous =>
          previous.map(item => (item.id === row.id ? { ...item, status: 'failed', error: message } : item))
        );
      }
    },
    [modality, studyUid, patientId, patientName]
  );

  const addFiles = useCallback(
    (fileList: FileList | File[]) => {
      const incoming = Array.from(fileList);
      if (incoming.length === 0) return;

      const newRows: QueueRow[] = incoming.map(file => ({
        id: makeId(),
        file,
        kind: isVolumeFile(file.name) ? 'volume' : 'dicom',
        status: 'pending',
      }));

      setRows(previous => [...newRows, ...previous]);
      newRows.forEach(row => {
        startUpload(row);
      });
    },
    [startUpload]
  );

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragActive(false);
      if (event.dataTransfer?.files?.length) {
        addFiles(event.dataTransfer.files);
      }
    },
    [addFiles]
  );

  const removeRow = (id: string) => {
    setRows(previous => previous.filter(row => row.id !== id));
  };

  const clearCompleted = () => {
    setRows(previous => previous.filter(row => row.status !== 'completed'));
  };

  const summary = rows.reduce(
    (acc, row) => {
      if (row.status === 'completed') acc.done += 1;
      else if (row.status === 'failed') acc.failed += 1;
      else acc.active += 1;
      return acc;
    },
    { done: 0, failed: 0, active: 0 }
  );

  return (
    <div className="relative min-h-screen w-full overflow-y-auto bg-black font-sans text-white">
      <div
        className="pointer-events-none absolute inset-0 opacity-20"
        style={{ backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)', backgroundSize: '30px 30px' }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-60"
        style={{
          background:
            'radial-gradient(circle at 20% 0%, rgba(90, 204, 230, 0.08), transparent 55%)',
        }}
      />

      <div className="relative z-10 mx-auto max-w-6xl px-6 py-10">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="mb-6 flex items-center gap-2 text-sm font-medium text-[#91b9cd] transition-colors hover:text-[#5acce6]"
        >
          <ArrowLeftIcon size={16} />
          Back
        </button>

        <div className="mb-8 flex items-start gap-4">
          <div className="rounded-xl border border-[#023a7d] bg-[#151a1f] p-2.5 shadow-lg shadow-[#5acce6]/10">
            <UploadCloudIcon size={26} className="text-[#5acce6]" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="text-3xl font-bold text-white">Upload Studies</h1>
            <p className="mt-1 max-w-xl text-sm text-[#566a84]">
              Add DICOM series straight to the archive, or drop a NIfTI, NRRD, or MetaImage
              volume and it will be converted into a real DICOM series automatically.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
          {/* Settings panel */}
          <div className="flex flex-col gap-4">
            <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f] p-5">
              <div className="mb-1 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">
                Volume modality
              </div>
              <p className="mb-3 text-xs text-[#566a84]">
                Applies to non-DICOM files only. Cohort search filters on this — never left as a
                guess.
              </p>
              <div className="grid grid-cols-2 gap-2">
                {MODALITIES.map(option => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setModality(option.value)}
                    title={option.hint}
                    className={`rounded-lg border px-3 py-2 text-left text-sm font-semibold transition-colors ${
                      modality === option.value
                        ? 'border-[#5acce6] bg-[#0d2847] text-[#5acce6]'
                        : 'border-[#3c5d80] bg-[#0f1724] text-[#91b9cd] hover:border-[#5acce6]/60'
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              {modality === 'OT' && (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-[#f8ce46]/30 bg-[#3b2d1c] p-2.5 text-xs text-[#f8ce46]">
                  <AlertTriangleIcon size={14} className="mt-0.5 shrink-0" />
                  <span>
                    Stored as Secondary Capture. Segmentation may not be able to use this series.
                  </span>
                </div>
              )}
            </div>

            <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f] p-5">
              <button
                type="button"
                onClick={() => setAdvancedOpen(open => !open)}
                className="flex w-full items-center justify-between text-xs font-bold uppercase tracking-wider text-[#91b9cd]"
              >
                Attach to existing study
                <ChevronDownIcon
                  size={16}
                  className={`transition-transform ${advancedOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {advancedOpen && (
                <div className="mt-3 flex flex-col gap-3">
                  <p className="text-xs text-[#566a84]">
                    Optional. Leave blank and a new patient is created from the file itself —
                    re-uploading the same file always resolves to the same patient.
                  </p>
                  <LabeledInput label="Study UID" value={studyUid} onChange={setStudyUid} mono />
                  <LabeledInput label="Patient ID" value={patientId} onChange={setPatientId} mono />
                  <LabeledInput label="Patient name" value={patientName} onChange={setPatientName} />
                </div>
              )}
            </div>

            {maxUploadBytes && (
              <p className="px-1 text-xs text-[#566a84]">
                Up to {(maxUploadBytes / 1024 / 1024 / 1024).toFixed(1)} GB per file · .dcm ·{' '}
                {VOLUME_EXTENSIONS.join(' · ')}
              </p>
            )}
          </div>

          {/* Dropzone + queue */}
          <div className="flex flex-col gap-5">
            <div
              onDragOver={event => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-14 text-center transition-colors ${
                dragActive
                  ? 'border-[#5acce6] bg-[#0d2847]'
                  : 'border-[#3c5d80] bg-[#0f1724] hover:border-[#5acce6]/60'
              }`}
            >
              <UploadCloudIcon size={34} className="text-[#5acce6]" strokeWidth={1.5} />
              <div>
                <div className="text-base font-semibold text-white">
                  Drag and drop files here
                </div>
                <div className="mt-1 text-sm text-[#566a84]">
                  or click to browse — DICOM and volume files can be mixed in one drop
                </div>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={event => {
                  if (event.target.files) addFiles(event.target.files);
                  event.target.value = '';
                }}
              />
            </div>

            {rows.length > 0 && (
              <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f]">
                <div className="flex items-center justify-between border-b border-[#3c5d80] px-5 py-3">
                  <div className="flex items-center gap-3 text-xs font-semibold text-[#91b9cd]">
                    <span>{rows.length} file{rows.length === 1 ? '' : 's'}</span>
                    {summary.active > 0 && <StatusPill tone="active">{summary.active} in progress</StatusPill>}
                    {summary.done > 0 && <StatusPill tone="ok">{summary.done} stored</StatusPill>}
                    {summary.failed > 0 && <StatusPill tone="error">{summary.failed} failed</StatusPill>}
                  </div>
                  {summary.done > 0 && (
                    <button
                      type="button"
                      onClick={clearCompleted}
                      className="text-xs font-semibold text-[#566a84] hover:text-[#91b9cd]"
                    >
                      Clear stored
                    </button>
                  )}
                </div>

                <div className="flex flex-col divide-y divide-[#243244]">
                  {rows.map(row => (
                    <QueueRowView key={row.id} row={row} onRemove={() => removeRow(row.id)} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  mono,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  mono?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-[#566a84]">
        {label}
      </span>
      <input
        value={value}
        onChange={event => onChange(event.target.value)}
        className={`rounded-lg border border-[#3c5d80] bg-[#0f1724] px-3 py-2 text-sm text-white placeholder:text-[#3c5d80] focus:border-[#5acce6] focus:outline-none ${
          mono ? 'font-mono text-xs' : ''
        }`}
      />
    </label>
  );
}

function StatusPill({
  tone,
  children,
}: {
  tone: 'muted' | 'active' | 'ok' | 'error';
  children: React.ReactNode;
}) {
  const toneClass = {
    muted: 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]',
    active: 'border-[#5acce6]/30 bg-[#0d2847] text-[#5acce6]',
    ok: 'border-[#6fe5b8]/30 bg-[#0d3320] text-[#6fe5b8]',
    error: 'border-[#ff6b6b]/30 bg-[#2a1313] text-[#ff6b6b]',
  }[tone];
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-[10px] font-bold border ${toneClass}`}>
      {children}
    </span>
  );
}

function QueueRowView({ row, onRemove }: { row: QueueRow; onRemove: () => void }) {
  const meta = STATUS_META[row.status];
  const detail = rowDetail(row) || row.error;

  return (
    <div className="flex items-center gap-4 px-5 py-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#0f1724] text-[#5acce6]">
        {row.kind === 'volume' ? <FileStackIcon size={16} /> : <FileTextIcon size={16} />}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-white">{row.file.name}</span>
          <StatusPill tone="muted">{fileKindLabel(row.file.name)}</StatusPill>
        </div>
        {detail && (
          <div
            className={`mt-0.5 truncate text-xs ${
              row.status === 'failed' ? 'text-[#ff6b6b]' : 'text-[#566a84]'
            }`}
          >
            {detail}
          </div>
        )}
      </div>

      <StatusIcon tone={meta.tone} />
      <StatusPill tone={meta.tone}>{meta.label}</StatusPill>

      <button
        type="button"
        onClick={onRemove}
        className="text-[#566a84] transition-colors hover:text-[#ff6b6b]"
        aria-label={`Remove ${row.file.name}`}
      >
        <Trash2Icon size={15} />
      </button>
    </div>
  );
}

function StatusIcon({ tone }: { tone: 'muted' | 'active' | 'ok' | 'error' }) {
  if (tone === 'active') {
    return <Loader2Icon size={16} className="animate-spin text-[#5acce6]" />;
  }
  if (tone === 'ok') {
    return <CheckCircle2Icon size={16} className="text-[#6fe5b8]" />;
  }
  if (tone === 'error') {
    return <XCircleIcon size={16} className="text-[#ff6b6b]" />;
  }
  return <div className="h-2 w-2 rounded-full bg-[#3c5d80]" />;
}
