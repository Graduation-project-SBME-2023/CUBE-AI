import {
  UploadError,
  fetchIngestJob,
  isVolumeFile,
  pollIngestJob,
  uploadMedicalFile,
} from './uploadMedicalFile';

jest.mock('./runtimeUrls', () => ({
  getAgentsBaseUrl: () => '/agents',
  getPacsBaseUrl: () => '/pacs',
}));

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: 'test',
  json: async () => body,
});

const makeFile = name => new File(['volume-bytes'], name, { type: 'application/octet-stream' });

describe('isVolumeFile', () => {
  it.each(['scan.nii', 'scan.nii.gz', 'scan.NII.GZ', 'a.nrrd', 'a.mha', 'a.mhd', 'a.nhdr'])(
    'treats %s as a volume file',
    name => {
      expect(isVolumeFile(name)).toBe(true);
    }
  );

  it.each(['image.dcm', 'image', 'notes.txt', 'archive.zip', ''])(
    'does not treat %s as a volume file',
    name => {
      expect(isVolumeFile(name)).toBe(false);
    }
  );
});

describe('uploadMedicalFile routing', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('sends DICOM straight to Orthanc, exactly as before', async () => {
    global.fetch.mockResolvedValue(jsonResponse({}, 200));
    const file = makeFile('image.dcm');

    const result = await uploadMedicalFile(file);

    expect(result).toEqual({ kind: 'dicom' });
    // Index access rather than array destructuring: this repo's
    // babel-plugin-transform-regenerator crashes on destructuring inside an
    // async function.
    const call = global.fetch.mock.calls[0];
    expect(call[0]).toBe('/pacs/instances');
    expect(call[1].method).toBe('POST');
    expect(call[1].headers['Content-Type']).toBe('application/dicom');
    expect(call[1].body).toBe(file);
  });

  it('sends a volume file to the ingestion endpoint instead', async () => {
    global.fetch.mockResolvedValue(
      jsonResponse({ job_id: 'abc123', status: 'queued', duplicate: false })
    );

    const result = await uploadMedicalFile(makeFile('scan.nii.gz'), { modality: 'CT' });

    expect(result).toEqual({
      kind: 'volume',
      jobId: 'abc123',
      status: 'queued',
      duplicate: false,
    });
    const call = global.fetch.mock.calls[0];
    expect(call[0]).toBe('/agents/ingest/nifti');
    expect(call[1].body.get('modality')).toBe('CT');
    expect(call[1].body.get('file').name).toBe('scan.nii.gz');
  });

  it('refuses a volume upload with no modality rather than guessing one', async () => {
    await expect(uploadMedicalFile(makeFile('scan.nii.gz'), {})).rejects.toThrow(
      /modality must be selected/i
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('passes optional placement fields through and omits empty ones', async () => {
    global.fetch.mockResolvedValue(jsonResponse({ job_id: 'j', status: 'queued' }));

    await uploadMedicalFile(makeFile('scan.nrrd'), {
      modality: 'MR',
      studyUid: '1.2.3',
      patientId: '',
    });

    const form = global.fetch.mock.calls[0][1].body;
    expect(form.get('study_uid')).toBe('1.2.3');
    expect(form.has('patient_id')).toBe(false);
    expect(form.has('patient_name')).toBe(false);
  });

  it('surfaces the server reason when an upload is rejected', async () => {
    global.fetch.mockResolvedValue(
      jsonResponse({ detail: 'Unsupported modality' }, 400)
    );

    await expect(uploadMedicalFile(makeFile('scan.nii.gz'), { modality: 'CT' })).rejects.toThrow(
      'Unsupported modality'
    );
  });

  it('reports the status code so a size limit can be handled specifically', async () => {
    global.fetch.mockResolvedValue(jsonResponse({ detail: 'too big' }, 413));

    await expect(
      uploadMedicalFile(makeFile('scan.nii.gz'), { modality: 'CT' })
    ).rejects.toMatchObject({ status: 413 });
  });
});

describe('job polling', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  it('reads a job by id', async () => {
    global.fetch.mockResolvedValue(jsonResponse({ job_id: 'abc', status: 'queued' }));

    await expect(fetchIngestJob('abc')).resolves.toMatchObject({ status: 'queued' });
    expect(global.fetch.mock.calls[0][0]).toBe('/agents/ingest/jobs/abc');
  });

  it('follows a job until it completes and reports every step', async () => {
    global.fetch
      .mockResolvedValueOnce(jsonResponse({ job_id: 'j', status: 'converting' }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'j', status: 'uploading' }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'j', status: 'completed' }));
    const seen = [];

    const job = await pollIngestJob('j', { intervalMs: 0, onUpdate: j => seen.push(j.status) });

    expect(job.status).toBe('completed');
    expect(seen).toEqual(['converting', 'uploading', 'completed']);
  });

  it('stops on failure rather than polling forever', async () => {
    global.fetch.mockResolvedValue(
      jsonResponse({ job_id: 'j', status: 'failed', error: 'geometry mismatch' })
    );

    const job = await pollIngestJob('j', { intervalMs: 0 });

    expect(job.status).toBe('failed');
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('gives up once the timeout passes', async () => {
    global.fetch.mockResolvedValue(jsonResponse({ job_id: 'j', status: 'converting' }));

    await expect(pollIngestJob('j', { intervalMs: 0, timeoutMs: -1 })).rejects.toBeInstanceOf(
      UploadError
    );
  });
});
