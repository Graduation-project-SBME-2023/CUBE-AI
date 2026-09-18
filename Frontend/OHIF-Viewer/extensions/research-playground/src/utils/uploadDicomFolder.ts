/**
 * Folder upload for DICOM, the way Orthanc Explorer does it.
 *
 * A DICOM study is not one file: it is a directory of per-slice instances, and
 * a research export is usually a directory of those directories. Sending them
 * one at a time through the single-file picker is unworkable at a few hundred
 * slices, and nothing in the viewer could group them.
 *
 * Nothing here decides what a study is. Every instance already carries its
 * StudyInstanceUID and SeriesInstanceUID, and Orthanc files each POSTed
 * instance under those tags, so the archive ends up correctly grouped whatever
 * order the files arrive in. The folder structure is used only to label
 * progress with something the person recognises -- their own folder names --
 * and the study ids Orthanc reports back are what the result actually counts.
 */

import { getPacsApiBaseUrl } from './apiBase';
import { isVolumeFile, UploadError } from './uploadMedicalFile';

/** Extensions that are definitely not DICOM, so they are skipped in a folder. */
const NON_DICOM_EXTENSIONS = [
  '.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml',
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.pdf',
  '.zip', '.gz', '.tar', '.7z', '.rar',
  '.xlsx', '.xls', '.doc', '.docx', '.html', '.htm', '.log', '.ini',
];

/** Files a file manager leaves behind that are never study data. */
const JUNK_NAMES = ['.ds_store', 'thumbs.db', 'desktop.ini', '.gitkeep'];

export interface FolderFile {
  file: File;
  /** Path within the chosen folder, e.g. "PatientA/series1/IM0001". */
  relativePath: string;
}

export interface DicomGroup {
  id: string;
  /** Folder the files came from; a label, not an identity. */
  label: string;
  files: FolderFile[];
}

export interface GroupProgress {
  groupId: string;
  uploaded: number;
  failed: number;
  total: number;
  /** StudyInstanceUIDs Orthanc filed these instances under. */
  studyIds: string[];
}

/**
 * Is this plausibly a DICOM instance?
 *
 * DICOM files frequently have no extension at all, or use one of several
 * conventions, so the test is negative: reject what is definitely something
 * else and let the rest through. A file that is not DICOM after all is
 * rejected by Orthanc with a 400, which the caller reports on that file alone.
 */
export function looksLikeDicom(filename: string): boolean {
  const name = String(filename || '').toLowerCase();
  const base = name.split('/').pop() || name;
  if (!base || base.startsWith('.') || JUNK_NAMES.includes(base)) {
    return false;
  }
  if (isVolumeFile(base)) {
    return false;
  }
  return !NON_DICOM_EXTENSIONS.some(extension => base.endsWith(extension));
}

/** Read the files under a folder chosen with a `webkitdirectory` input. */
export function filesFromInput(fileList: FileList | File[]): FolderFile[] {
  return Array.from(fileList)
    .map(file => ({
      file,
      // webkitRelativePath is "" for a plain multi-file selection.
      relativePath: (file as any).webkitRelativePath || file.name,
    }))
    .filter(entry => looksLikeDicom(entry.relativePath));
}

/**
 * Read the files under dropped folders.
 *
 * A drop gives directory entries rather than files, so this walks them.
 * Directories are read in pages: `readEntries` returns at most 100 entries per
 * call and signals the end with an empty batch, so a single call silently
 * truncates a large series.
 */
export async function filesFromDrop(dataTransfer: DataTransfer): Promise<FolderFile[]> {
  const roots: any[] = [];
  for (const item of Array.from(dataTransfer.items || [])) {
    const entry = (item as any).webkitGetAsEntry?.();
    if (entry) {
      roots.push(entry);
    }
  }

  // No directory support (or a plain file drop): fall back to the file list.
  if (roots.length === 0) {
    return filesFromInput(dataTransfer.files || []);
  }

  const collected: FolderFile[] = [];

  const readDirectory = (reader: any): Promise<any[]> =>
    new Promise((resolve, reject) => reader.readEntries(resolve, reject));

  const walk = async (entry: any, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file: File = await new Promise((resolve, reject) => entry.file(resolve, reject));
      const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (looksLikeDicom(relativePath)) {
        collected.push({ file, relativePath });
      }
      return;
    }
    if (!entry.isDirectory) {
      return;
    }
    const reader = entry.createReader();
    const nested = prefix ? `${prefix}/${entry.name}` : entry.name;
    for (;;) {
      const batch = await readDirectory(reader);
      if (!batch.length) {
        break;
      }
      for (const child of batch) {
        await walk(child, nested);
      }
    }
  };

  for (const root of roots) {
    await walk(root, '');
  }
  return collected;
}

/**
 * Group files by the folder they came from.
 *
 * The grouping is presentational. Where a folder holds a study per
 * subdirectory, each subdirectory becomes its own group; where it is one flat
 * study, everything lands in a single group. Either way Orthanc re-files the
 * instances by their own tags, so a mis-grouped folder still stores correctly.
 */
export function groupByFolder(files: FolderFile[]): DicomGroup[] {
  const groups = new Map<string, DicomGroup>();

  for (const entry of files) {
    const segments = entry.relativePath.split('/').filter(Boolean);
    // Drop the filename; keep at most the two directory levels above it, which
    // is where a "patient/series" export puts its meaningful names.
    const directories = segments.slice(0, -1);
    const label = directories.length ? directories.slice(-2).join(' / ') : 'Selected files';
    const key = directories.join('/') || '__flat__';

    let group = groups.get(key);
    if (!group) {
      group = { id: `g-${groups.size}-${key}`, label, files: [] };
      groups.set(key, group);
    }
    group.files.push(entry);
  }

  return Array.from(groups.values());
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === 'string') {
      return body.detail;
    }
    return JSON.stringify(body);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

/** Store one instance, returning the study Orthanc filed it under. */
async function postInstance(file: File, signal?: AbortSignal): Promise<string | null> {
  const response = await fetch(`${getPacsApiBaseUrl()}/instances`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/dicom' },
    body: file,
    signal,
  });
  if (!response.ok) {
    throw new UploadError(await describeFailure(response), response.status);
  }
  try {
    const body = await response.json();
    return body?.ParentStudy ?? null;
  } catch {
    return null;
  }
}

/**
 * How many instances to send at once.
 *
 * Sequential upload of a 500-slice study is needlessly slow, and unbounded
 * parallelism is what makes Orthanc's HTTP server drop connections partway
 * through -- the same failure the ingestion path takes a lease to avoid.
 */
const UPLOAD_CONCURRENCY = 4;

export interface UploadGroupOptions {
  signal?: AbortSignal;
  onProgress?: (progress: GroupProgress) => void;
}

/** Upload one group's instances, reporting progress as they land. */
export async function uploadDicomGroup(
  group: DicomGroup,
  options: UploadGroupOptions = {}
): Promise<GroupProgress> {
  const total = group.files.length;
  const studyIds = new Set<string>();
  const errors: string[] = [];
  let uploaded = 0;
  let failed = 0;
  let next = 0;

  const report = () =>
    options.onProgress?.({
      groupId: group.id,
      uploaded,
      failed,
      total,
      studyIds: Array.from(studyIds),
    });

  const worker = async () => {
    for (;;) {
      const index = next++;
      if (index >= total) {
        return;
      }
      if (options.signal?.aborted) {
        return;
      }
      try {
        const studyId = await postInstance(group.files[index].file, options.signal);
        if (studyId) {
          studyIds.add(studyId);
        }
        uploaded += 1;
      } catch (error) {
        failed += 1;
        // One unreadable file must not abandon the rest of the study; the
        // count is surfaced instead, with the first reason kept as the cause.
        if (errors.length < 1) {
          errors.push(error instanceof Error ? error.message : 'Upload failed');
        }
      }
      report();
    }
  };

  report();
  await Promise.all(
    Array.from({ length: Math.min(UPLOAD_CONCURRENCY, total || 1) }, worker)
  );

  if (uploaded === 0 && failed > 0) {
    throw new UploadError(errors[0] || 'Every file in this folder failed to upload', 400);
  }

  return { groupId: group.id, uploaded, failed, total, studyIds: Array.from(studyIds) };
}
