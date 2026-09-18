/**
 * One upload entry point for both DICOM and volume files.
 *
 * DICOM keeps its existing route: straight to Orthanc, unchanged. A volume file
 * (NIfTI/NRRD/MetaImage) cannot be stored by Orthanc as-is, so it goes to the
 * agent system, which converts it into a DICOM series and stores that instead.
 * Either way the file ends up in the same place, which is why the caller only
 * has to hand over a File.
 */

import { getAgentsBaseUrl, getPacsBaseUrl } from './runtimeUrls';

/** Volume formats SimpleITK reads with identical geometry handling. */
export const VOLUME_EXTENSIONS = ['.nii.gz', '.nii', '.nrrd', '.nhdr', '.mha', '.mhd'];

/**
 * Modality is never inferred. Cohort queries filter on it, so a guess would
 * quietly hide the series, and calling something CT when its units are unknown
 * is a scientific error rather than a formatting one.
 */
export type IngestModality = 'CT' | 'MR' | 'PT' | 'OT';

export type IngestJobStatus =
  | 'queued'
  | 'converting'
  | 'uploading'
  | 'verifying'
  | 'syncing'
  | 'completed'
  | 'failed';

export interface IngestJob {
  job_id: string;
  status: IngestJobStatus;
  filename?: string;
  modality?: string;
  slice_count?: number;
  uploaded_count?: number;
  series_instance_uid?: string | null;
  study_instance_uid?: string | null;
  patient_id?: string | null;
  warnings?: string[];
  error?: string | null;
  verification?: Record<string, unknown> | null;
}

export interface VolumeUploadOptions {
  modality: IngestModality;
  /** Attach to an existing study so the series lands beside that patient's scans. */
  studyUid?: string;
  patientId?: string;
  patientName?: string;
  seriesDescription?: string;
  signal?: AbortSignal;
}

export interface UploadResult {
  kind: 'dicom' | 'volume';
  /** Only volume uploads run in the background and produce a job. */
  jobId?: string;
  status?: IngestJobStatus;
  duplicate?: boolean;
}

export class UploadError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'UploadError';
    this.status = status;
  }
}

export function isVolumeFile(filename: string): boolean {
  const lowered = String(filename || '').toLowerCase();
  return VOLUME_EXTENSIONS.some(extension => lowered.endsWith(extension));
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === 'string') {
      return body.detail;
    }
    return JSON.stringify(body);
  } catch (error) {
    // Binding the error is required: this repo's babel regenerator transform
    // crashes on an optional catch binding inside an async function.
    return response.statusText || `HTTP ${response.status}`;
  }
}

/** Store a DICOM file in Orthanc. This is the pre-existing path, unchanged. */
export async function uploadDicomFile(file: File, signal?: AbortSignal): Promise<UploadResult> {
  const response = await fetch(`${getPacsBaseUrl()}/instances`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/dicom' },
    body: file,
    signal,
  });

  if (!response.ok) {
    throw new UploadError(await describeFailure(response), response.status);
  }
  return { kind: 'dicom' };
}

/** Hand a volume file to the agent system for conversion into a DICOM series. */
export async function uploadVolumeFile(
  file: File,
  options: VolumeUploadOptions
): Promise<UploadResult> {
  if (!options?.modality) {
    throw new UploadError('A modality must be selected before uploading a volume file', 400);
  }

  const form = new FormData();
  form.append('file', file, file.name);
  form.append('modality', options.modality);
  if (options.studyUid) {
    form.append('study_uid', options.studyUid);
  }
  if (options.patientId) {
    form.append('patient_id', options.patientId);
  }
  if (options.patientName) {
    form.append('patient_name', options.patientName);
  }
  if (options.seriesDescription) {
    form.append('series_description', options.seriesDescription);
  }

  const response = await fetch(`${getAgentsBaseUrl()}/ingest/nifti`, {
    method: 'POST',
    body: form,
    signal: options.signal,
  });

  if (!response.ok) {
    throw new UploadError(await describeFailure(response), response.status);
  }

  const body = await response.json();
  return {
    kind: 'volume',
    jobId: body.job_id,
    status: body.status,
    duplicate: Boolean(body.duplicate),
  };
}

/** Route a file by type. DICOM behaviour is untouched; volumes are converted. */
export async function uploadMedicalFile(
  file: File,
  options?: Partial<VolumeUploadOptions>
): Promise<UploadResult> {
  if (isVolumeFile(file.name)) {
    return uploadVolumeFile(file, options as VolumeUploadOptions);
  }
  return uploadDicomFile(file, options?.signal);
}

export async function fetchIngestJob(jobId: string, signal?: AbortSignal): Promise<IngestJob> {
  const response = await fetch(
    `${getAgentsBaseUrl()}/ingest/jobs/${encodeURIComponent(jobId)}`,
    { signal }
  );
  if (!response.ok) {
    throw new UploadError(await describeFailure(response), response.status);
  }
  return response.json();
}

export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (job: IngestJob) => void;
  signal?: AbortSignal;
}

const TERMINAL_STATUSES: IngestJobStatus[] = ['completed', 'failed'];

/**
 * Follow a conversion until it finishes.
 *
 * Conversion happens in the background because a large volume takes far longer
 * than a request should, so progress is only visible by polling the job.
 */
export async function pollIngestJob(jobId: string, options: PollOptions = {}): Promise<IngestJob> {
  const intervalMs = options.intervalMs ?? 2000;
  const timeoutMs = options.timeoutMs ?? 30 * 60 * 1000;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    const job = await fetchIngestJob(jobId, options.signal);
    options.onUpdate?.(job);

    if (TERMINAL_STATUSES.includes(job.status)) {
      return job;
    }
    if (Date.now() >= deadline) {
      throw new UploadError(`Ingestion job ${jobId} did not finish within the timeout`, 504);
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
}
