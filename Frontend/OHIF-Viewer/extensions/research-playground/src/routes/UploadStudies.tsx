import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  UploadCloud,
  FileText,
  FileStack,
  FolderOpen,
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
  uploadVolumeFile,
  UploadError,
  VOLUME_EXTENSIONS,
  isVolumeFile,
  type IngestJob,
  type IngestModality,
} from '../utils/uploadMedicalFile';
import {
  filesFromDrop,
  filesFromInput,
  groupByFolder,
  uploadDicomGroup,
  type DicomGroup,
} from '../utils/uploadDicomFolder';
import { getAgentsApiBaseUrl } from '../utils/apiBase';

// Icon aliases for lucide-react to satisfy JSX typing in this project.
const ArrowLeftIcon: any = ArrowLeft;
const UploadCloudIcon: any = UploadCloud;
const FileTextIcon: any = FileText;
const FileStackIcon: any = FileStack;
const FolderOpenIcon: any = FolderOpen;
const CheckCircle2Icon: any = CheckCircle2;
const XCircleIcon: any = XCircle;
const Loader2Icon: any = Loader2;
const Trash2Icon: any = Trash2;
const ChevronDownIcon: any = ChevronDown;
const AlertTriangleIcon: any = AlertTriangle;

type Tab = 'dicom' | 'volume';

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

/** A volume file awaiting, or undergoing, conversion. */
interface VolumeRow {
  id: string;
  file: File;
  status: RowStatus;
  /** Chosen per row: several files selected together are several studies. */
  bodyPart: string;
  error?: string;
  jobId?: string;
  job?: IngestJob;
}

/** One folder of DICOM instances, tracked as a unit. */
interface DicomRow {
  id: string;
  label: string;
  group: DicomGroup;
  status: RowStatus;
  uploaded: number;
  failed: number;
  total: number;
  studyCount: number;
  error?: string;
}

interface BodyPartOption {
  value: string;
  group: string;
}

const MODALITIES: { value: IngestModality; label: string; hint: string }[] = [
  { value: 'CT', label: 'CT', hint: 'Hounsfield-unit CT volume' },
  { value: 'MR', label: 'MR', hint: 'Magnetic resonance volume' },
  { value: 'OT', label: 'Other', hint: 'Unknown scale — hidden from CT/MR search' },
];

const POLL_INTERVAL_MS = 1500;

const STATUS_META: Record<RowStatus, { label: string; tone: 'muted' | 'active' | 'ok' | 'error' }> = {
  pending: { label: 'Ready', tone: 'muted' },
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

function volumeKindLabel(filename: string): string {
  const lower = filename.toLowerCase();
  const match = VOLUME_EXTENSIONS.find(ext => lower.endsWith(ext));
  return match ? match.replace('.', '').toUpperCase() : 'VOLUME';
}

function volumeRowDetail(row: VolumeRow): string | null {
  const job = row.job;
  if (!job) {
    return null;
  }
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

function dicomRowDetail(row: DicomRow): string | null {
  if (row.status === 'completed') {
    const studies = row.studyCount === 1 ? '1 study' : `${row.studyCount} studies`;
    const failed = row.failed ? ` · ${row.failed} rejected` : '';
    return `${row.uploaded} of ${row.total} instances · ${studies}${failed}`;
  }
  if (row.status === 'uploading') {
    return `${row.uploaded} / ${row.total} instances`;
  }
  if (row.status === 'failed') {
    return row.error || 'Upload failed';
  }
  return `${row.total} instance${row.total === 1 ? '' : 's'}`;
}

export default function UploadStudies() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('dicom');

  const [volumeRows, setVolumeRows] = useState<VolumeRow[]>([]);
  const [dicomRows, setDicomRows] = useState<DicomRow[]>([]);

  const [modality, setModality] = useState<IngestModality>('CT');
  const [dragActive, setDragActive] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [studyUid, setStudyUid] = useState('');
  const [patientId, setPatientId] = useState('');
  const [patientName, setPatientName] = useState('');
  const [maxUploadBytes, setMaxUploadBytes] = useState<number | null>(null);
  const [bodyParts, setBodyParts] = useState<BodyPartOption[]>([]);

  const volumeInputRef = useRef<HTMLInputElement | null>(null);
  const dicomFileInputRef = useRef<HTMLInputElement | null>(null);
  const dicomFolderInputRef = useRef<HTMLInputElement | null>(null);
  const volumeRowsRef = useRef<VolumeRow[]>([]);
  volumeRowsRef.current = volumeRows;

  useEffect(() => {
    let cancelled = false;
    fetch(`${getAgentsApiBaseUrl()}/ingest/formats`)
      .then(response => (response.ok ? response.json() : null))
      .then(body => {
        if (cancelled || !body) {
          return;
        }
        if (body.max_upload_bytes) {
          setMaxUploadBytes(body.max_upload_bytes);
        }
        // Served by the backend so the list can only offer values the
        // converter accepts and anatomy validation recognises.
        if (Array.isArray(body.body_parts)) {
          setBodyParts(body.body_parts);
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
      const pending = volumeRowsRef.current.filter(
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

      setVolumeRows(previous =>
        previous.map(row => {
          const update = updates.find(entry => entry.id === row.id);
          if (!update || !update.job) return row;
          return { ...row, job: update.job, status: jobStatusToRowStatus(update.job.status) };
        })
      );
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  // ── Volume uploads ────────────────────────────────────────────────────
  // Nothing is sent until the person presses Upload: the body part is chosen
  // per row, and the file has to reach the converter with it already decided.
  // BodyPartExamined is part of the series identity, so re-uploading to fix it
  // mints a second series rather than correcting the first.

  const addVolumeFiles = useCallback((fileList: FileList | File[]) => {
    const incoming = Array.from(fileList).filter(file => isVolumeFile(file.name));
    if (incoming.length === 0) return;
    setVolumeRows(previous => [
      ...incoming.map(file => ({
        id: makeId(),
        file,
        status: 'pending' as RowStatus,
        bodyPart: '',
      })),
      ...previous,
    ]);
  }, []);

  const startVolumeUpload = useCallback(
    async (row: VolumeRow) => {
      setVolumeRows(previous =>
        previous.map(item => (item.id === row.id ? { ...item, status: 'queued', error: undefined } : item))
      );
      try {
        const result = await uploadVolumeFile(row.file, {
          modality,
          bodyPart: row.bodyPart || undefined,
          studyUid: studyUid.trim() || undefined,
          patientId: patientId.trim() || undefined,
          patientName: patientName.trim() || undefined,
        });
        setVolumeRows(previous =>
          previous.map(item =>
            item.id === row.id
              ? { ...item, jobId: result.jobId, status: jobStatusToRowStatus(result.status) }
              : item
          )
        );
      } catch (error) {
        const message = error instanceof UploadError ? error.message : 'Upload failed';
        setVolumeRows(previous =>
          previous.map(item =>
            item.id === row.id ? { ...item, status: 'failed', error: message } : item
          )
        );
      }
    },
    [modality, studyUid, patientId, patientName]
  );

  const uploadAllVolumes = useCallback(() => {
    volumeRowsRef.current
      .filter(row => row.status === 'pending')
      .forEach(row => startVolumeUpload(row));
  }, [startVolumeUpload]);

  const setRowBodyPart = (id: string, value: string) => {
    setVolumeRows(previous =>
      previous.map(row => (row.id === id ? { ...row, bodyPart: value } : row))
    );
  };

  // ── DICOM folder uploads ──────────────────────────────────────────────

  const addDicomGroups = useCallback((groups: DicomGroup[]) => {
    if (groups.length === 0) return;
    const rows: DicomRow[] = groups.map(group => ({
      id: makeId(),
      label: group.label,
      group,
      status: 'uploading',
      uploaded: 0,
      failed: 0,
      total: group.files.length,
      studyCount: 0,
    }));
    setDicomRows(previous => [...rows, ...previous]);

    rows.forEach(async row => {
      try {
        const progress = await uploadDicomGroup(row.group, {
          onProgress: update => {
            setDicomRows(previous =>
              previous.map(item =>
                item.id === row.id
                  ? {
                      ...item,
                      uploaded: update.uploaded,
                      failed: update.failed,
                      studyCount: update.studyIds.length,
                    }
                  : item
              )
            );
          },
        });
        setDicomRows(previous =>
          previous.map(item =>
            item.id === row.id
              ? {
                  ...item,
                  status: 'completed',
                  uploaded: progress.uploaded,
                  failed: progress.failed,
                  studyCount: progress.studyIds.length,
                }
              : item
          )
        );
      } catch (error) {
        const message = error instanceof UploadError ? error.message : 'Upload failed';
        setDicomRows(previous =>
          previous.map(item =>
            item.id === row.id ? { ...item, status: 'failed', error: message } : item
          )
        );
      }
    });
  }, []);

  const handleDrop = useCallback(
    async (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragActive(false);
      const transfer = event.dataTransfer;
      if (!transfer) return;

      if (tab === 'volume') {
        if (transfer.files?.length) addVolumeFiles(transfer.files);
        return;
      }
      // transfer is captured above: a DataTransfer is only readable during the
      // handler, and this one yields while it walks the directory tree.
      const files = await filesFromDrop(transfer);
      addDicomGroups(groupByFolder(files));
    },
    [tab, addVolumeFiles, addDicomGroups]
  );

  const removeVolumeRow = (id: string) =>
    setVolumeRows(previous => previous.filter(row => row.id !== id));
  const removeDicomRow = (id: string) =>
    setDicomRows(previous => previous.filter(row => row.id !== id));

  const clearCompleted = () => {
    setVolumeRows(previous => previous.filter(row => row.status !== 'completed'));
    setDicomRows(previous => previous.filter(row => row.status !== 'completed'));
  };

  const activeRows: { status: RowStatus }[] = tab === 'volume' ? volumeRows : dicomRows;
  const summary = activeRows.reduce(
    (acc, row) => {
      if (row.status === 'completed') acc.done += 1;
      else if (row.status === 'failed') acc.failed += 1;
      else if (row.status === 'pending') acc.ready += 1;
      else acc.active += 1;
      return acc;
    },
    { done: 0, failed: 0, active: 0, ready: 0 }
  );

  const groupedBodyParts = useMemo(() => {
    const byGroup = new Map<string, BodyPartOption[]>();
    bodyParts.forEach(option => {
      const list = byGroup.get(option.group) || [];
      list.push(option);
      byGroup.set(option.group, list);
    });
    return Array.from(byGroup.entries());
  }, [bodyParts]);

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

        <div className="mb-6 flex items-start gap-4">
          <div className="rounded-xl border border-[#023a7d] bg-[#151a1f] p-2.5 shadow-lg shadow-[#5acce6]/10">
            <UploadCloudIcon size={26} className="text-[#5acce6]" strokeWidth={1.5} />
          </div>
          <div>
            <h1 className="text-3xl font-bold text-white">Upload Studies</h1>
            <p className="mt-1 max-w-xl text-sm text-[#566a84]">
              Send DICOM folders straight to the archive, or convert a NIfTI, NRRD or
              MetaImage volume into a real DICOM series.
            </p>
          </div>
        </div>

        {/* Two uploaders: DICOM arrives already grouped into studies, a volume
            file is a single series that has to be described before conversion. */}
        <div className="mb-6 inline-flex rounded-xl border border-[#3c5d80] bg-[#151a1f] p-1">
          <TabButton active={tab === 'dicom'} onClick={() => setTab('dicom')}>
            <FolderOpenIcon size={15} />
            DICOM folders
          </TabButton>
          <TabButton active={tab === 'volume'} onClick={() => setTab('volume')}>
            <FileStackIcon size={15} />
            NIfTI / NRRD / MetaImage
          </TabButton>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
          {/* Settings panel */}
          <div className="flex flex-col gap-4">
            {tab === 'dicom' ? (
              <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f] p-5">
                <div className="mb-1 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">
                  How grouping works
                </div>
                <p className="text-xs leading-relaxed text-[#566a84]">
                  Every instance already names its own study and series, and the archive
                  files it under those. Pick one folder per study, or a parent folder of
                  them — the folder names are only used to label progress here.
                </p>
              </div>
            ) : (
              <>
                <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f] p-5">
                  <div className="mb-1 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">
                    Modality
                  </div>
                  <p className="mb-3 text-xs text-[#566a84]">
                    Cohort search filters on this — never left as a guess.
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    {MODALITIES.map(option => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setModality(option.value)}
                        title={option.hint}
                        className={`rounded-lg border px-3 py-2 text-center text-sm font-semibold transition-colors ${
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
                        Stored as Secondary Capture. Segmentation may not be able to use this
                        series.
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
              </>
            )}

            {maxUploadBytes && (
              <p className="px-1 text-xs text-[#566a84]">
                Up to {(maxUploadBytes / 1024 / 1024 / 1024).toFixed(1)} GB per file ·{' '}
                {tab === 'dicom' ? 'DICOM instances' : VOLUME_EXTENSIONS.join(' · ')}
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
              className={`flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
                dragActive
                  ? 'border-[#5acce6] bg-[#0d2847]'
                  : 'border-[#3c5d80] bg-[#0f1724]'
              }`}
            >
              <UploadCloudIcon size={34} className="text-[#5acce6]" strokeWidth={1.5} />
              <div>
                <div className="text-base font-semibold text-white">
                  {tab === 'dicom' ? 'Drag folders here' : 'Drag volume files here'}
                </div>
                <div className="mt-1 text-sm text-[#566a84]">
                  {tab === 'dicom'
                    ? 'a study folder, or a folder containing one per study'
                    : VOLUME_EXTENSIONS.join(' · ')}
                </div>
              </div>

              <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
                {tab === 'dicom' ? (
                  <>
                    <PickerButton onClick={() => dicomFolderInputRef.current?.click()} primary>
                      <FolderOpenIcon size={15} />
                      Choose folder
                    </PickerButton>
                    <PickerButton onClick={() => dicomFileInputRef.current?.click()}>
                      <FileTextIcon size={15} />
                      Choose files
                    </PickerButton>
                  </>
                ) : (
                  <PickerButton onClick={() => volumeInputRef.current?.click()} primary>
                    <FileStackIcon size={15} />
                    Choose volume files
                  </PickerButton>
                )}
              </div>

              {/* webkitdirectory is not in React's typings, so it is spread in. */}
              <input
                ref={dicomFolderInputRef}
                type="file"
                multiple
                className="hidden"
                {...({ webkitdirectory: '', directory: '' } as any)}
                onChange={event => {
                  if (event.target.files) {
                    addDicomGroups(groupByFolder(filesFromInput(event.target.files)));
                  }
                  event.target.value = '';
                }}
              />
              <input
                ref={dicomFileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={event => {
                  if (event.target.files) {
                    addDicomGroups(groupByFolder(filesFromInput(event.target.files)));
                  }
                  event.target.value = '';
                }}
              />
              <input
                ref={volumeInputRef}
                type="file"
                multiple
                className="hidden"
                accept={VOLUME_EXTENSIONS.join(',')}
                onChange={event => {
                  if (event.target.files) addVolumeFiles(event.target.files);
                  event.target.value = '';
                }}
              />
            </div>

            {activeRows.length > 0 && (
              <div className="rounded-2xl border border-[#3c5d80] bg-[#151a1f]">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#3c5d80] px-5 py-3">
                  <div className="flex flex-wrap items-center gap-3 text-xs font-semibold text-[#91b9cd]">
                    <span>
                      {activeRows.length} {tab === 'dicom' ? 'folder' : 'file'}
                      {activeRows.length === 1 ? '' : 's'}
                    </span>
                    {summary.ready > 0 && <StatusPill tone="muted">{summary.ready} ready</StatusPill>}
                    {summary.active > 0 && <StatusPill tone="active">{summary.active} in progress</StatusPill>}
                    {summary.done > 0 && <StatusPill tone="ok">{summary.done} stored</StatusPill>}
                    {summary.failed > 0 && <StatusPill tone="error">{summary.failed} failed</StatusPill>}
                  </div>
                  <div className="flex items-center gap-3">
                    {tab === 'volume' && summary.ready > 0 && (
                      <button
                        type="button"
                        onClick={uploadAllVolumes}
                        className="rounded-lg border border-[#5acce6] bg-[#0d2847] px-3 py-1.5 text-xs font-bold text-[#5acce6] transition-colors hover:bg-[#123255]"
                      >
                        Upload {summary.ready} file{summary.ready === 1 ? '' : 's'}
                      </button>
                    )}
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
                </div>

                <div className="flex flex-col divide-y divide-[#243244]">
                  {tab === 'dicom'
                    ? dicomRows.map(row => (
                        <DicomRowView
                          key={row.id}
                          row={row}
                          onRemove={() => removeDicomRow(row.id)}
                        />
                      ))
                    : volumeRows.map(row => (
                        <VolumeRowView
                          key={row.id}
                          row={row}
                          groupedBodyParts={groupedBodyParts}
                          onBodyPart={value => setRowBodyPart(row.id, value)}
                          onUpload={() => startVolumeUpload(row)}
                          onRemove={() => removeVolumeRow(row.id)}
                        />
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

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${
        active ? 'bg-[#0d2847] text-[#5acce6]' : 'text-[#91b9cd] hover:text-white'
      }`}
    >
      {children}
    </button>
  );
}

function PickerButton({
  onClick,
  primary,
  children,
}: {
  onClick: () => void;
  primary?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold transition-colors ${
        primary
          ? 'border-[#5acce6] bg-[#0d2847] text-[#5acce6] hover:bg-[#123255]'
          : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#5acce6]/60'
      }`}
    >
      {children}
    </button>
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

function VolumeRowView({
  row,
  groupedBodyParts,
  onBodyPart,
  onUpload,
  onRemove,
}: {
  row: VolumeRow;
  groupedBodyParts: [string, BodyPartOption[]][];
  onBodyPart: (value: string) => void;
  onUpload: () => void;
  onRemove: () => void;
}) {
  const meta = STATUS_META[row.status];
  const detail = volumeRowDetail(row) || row.error;
  const editable = row.status === 'pending';

  return (
    <div className="flex flex-wrap items-center gap-4 px-5 py-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#0f1724] text-[#5acce6]">
        <FileStackIcon size={16} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-white">{row.file.name}</span>
          <StatusPill tone="muted">{volumeKindLabel(row.file.name)}</StatusPill>
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

      {/* Per row, because several files selected together become several
          studies — and a NIfTI header carries no anatomy to fall back on. */}
      <select
        value={row.bodyPart}
        disabled={!editable}
        onChange={event => onBodyPart(event.target.value)}
        title="Recorded as DICOM BodyPartExamined, and read by anatomy validation"
        className="w-40 rounded-lg border border-[#3c5d80] bg-[#0f1724] px-2 py-1.5 text-xs text-white focus:border-[#5acce6] focus:outline-none disabled:opacity-50"
      >
        <option value="">Organ — not set</option>
        {groupedBodyParts.map(([group, options]) => (
          <optgroup key={group} label={group}>
            {options.map(option => (
              <option key={option.value} value={option.value}>
                {option.value.charAt(0) + option.value.slice(1).toLowerCase()}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {editable && (
        <button
          type="button"
          onClick={onUpload}
          className="rounded-lg border border-[#3c5d80] px-3 py-1.5 text-xs font-semibold text-[#91b9cd] transition-colors hover:border-[#5acce6] hover:text-[#5acce6]"
        >
          Upload
        </button>
      )}

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

function DicomRowView({ row, onRemove }: { row: DicomRow; onRemove: () => void }) {
  const meta = STATUS_META[row.status];
  const detail = dicomRowDetail(row);
  const percent = row.total ? Math.round((row.uploaded / row.total) * 100) : 0;

  return (
    <div className="flex items-center gap-4 px-5 py-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#0f1724] text-[#5acce6]">
        <FolderOpenIcon size={16} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-white">{row.label}</span>
          <StatusPill tone="muted">DICOM</StatusPill>
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
        {row.status === 'uploading' && (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-[#1f2d40]">
            <div
              className="h-full rounded bg-[#5acce6] transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}
      </div>

      <StatusIcon tone={meta.tone} />
      <StatusPill tone={meta.tone}>{meta.label}</StatusPill>

      <button
        type="button"
        onClick={onRemove}
        className="text-[#566a84] transition-colors hover:text-[#ff6b6b]"
        aria-label={`Remove ${row.label}`}
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
