/**
 * The one API client. Relative paths only (`/api/...`, `/p/...`): no base URL, no env var, no
 * origin. The server's `message` is developer English and is never read here beyond parsing the
 * envelope; only `code` leaves this module.
 *
 * `golden.ts` is the Demo Safe Mode transport (zero API calls): a `golden-{pack}` job
 * id, which only a sample pack picked with the toggle on produces, is answered from the static
 * bundle under `/samples/{pack}/golden/` and never reaches `/api/...`. A real upload never gets
 * one; when the backend is absent `createJob` says so instead of simulating a job.
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import {
  goldenExportFiles,
  goldenJobId,
  goldenJobState,
  isGoldenJobId,
  loadGolden,
} from './golden';
import type { components } from './types';

export type JobState = components['schemas']['JobState'];
export type ErrorCode = components['schemas']['ErrorCode'];
export type SamplePack = 'a' | 'b' | 'c';

/** A failed API call, reduced to the closed `code`; `null` when the body was not the envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | null;

  constructor(status: number, code: ErrorCode | null) {
    super(code ?? `http_${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

async function toApiError(response: Response): Promise<ApiError> {
  let code: ErrorCode | null = null;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && isRecord(body['error']) && typeof body['error']['code'] === 'string') {
      code = body['error']['code'] as ErrorCode;
    }
  } catch {
    // Not JSON: the status alone is all we know.
  }
  return new ApiError(response.status, code);
}

/**
 * `POST /api/jobs` answered 404/405: there is no API behind this origin (e.g. a static-only
 * preview build). Distinct from a 400 validation refusal, which is a real `ApiError` with a code.
 */
export class BackendUnavailableError extends Error {
  constructor() {
    super('backend_unavailable');
    this.name = 'BackendUnavailableError';
  }
}

export interface CreateJobInput {
  photos: File[];
  /** One caption per photo, same order as `photos`; empty string when none. */
  photoCaptions: string[];
  generalCaption: string;
  demoSafe: boolean;
  samplePack: SamplePack | null;
}

/** Photos per bundled sample pack. */
const SAMPLE_PACK_PHOTOS = 2;

export interface SamplePackFiles {
  photos: File[];
  /** Contents of the pack's `captions.txt`; empty when the pack ships none. */
  captions: string;
}

/**
 * Loads a bundled sample pack's photos so the real pipeline can run on them (Demo Safe Mode off).
 * The SPA catch-all answers a missing file with HTML and status 200, so the content type is the
 * proof that a real image came back.
 */
export async function fetchSamplePack(pack: SamplePack): Promise<SamplePackFiles> {
  const photos: File[] = [];
  for (let n = 1; n <= SAMPLE_PACK_PHOTOS; n += 1) {
    let response: Response;
    try {
      response = await fetch(`/samples/${pack}/photo${n}.jpg`);
    } catch {
      throw new ApiError(0, null);
    }
    const type = response.headers.get('content-type') ?? '';
    if (!response.ok || !type.startsWith('image/')) throw new ApiError(404, 'not_found');
    const blob = await response.blob();
    photos.push(new File([blob], `photo${n}.jpg`, { type: blob.type }));
  }
  let captions = '';
  try {
    const response = await fetch(`/samples/${pack}/captions.txt`);
    const type = response.headers.get('content-type') ?? '';
    if (response.ok && type.startsWith('text/plain')) captions = await response.text();
  } catch {
    // Captions are optional; the photos alone are enough to run.
  }
  return { photos, captions };
}

/** Creates a job and returns its id. Demo Safe Mode with a sample pack makes no request at all. */
export async function createJob(input: CreateJobInput): Promise<string> {
  if (input.demoSafe && input.samplePack !== null) {
    try {
      await loadGolden(input.samplePack);
    } catch {
      throw new ApiError(404, 'not_found');
    }
    return goldenJobId(input.samplePack);
  }

  const form = new FormData();
  input.photos.forEach((photo) => form.append('photos', photo, photo.name));
  form.append(
    'captions',
    JSON.stringify({ general: input.generalCaption, photos: input.photoCaptions }),
  );
  form.append('demo_safe', String(input.demoSafe));

  let response: Response;
  try {
    response = await fetch('/api/jobs', { method: 'POST', body: form });
  } catch {
    throw new ApiError(0, null);
  }
  if (response.ok) {
    const state = (await response.json()) as JobState;
    return state.job_id;
  }
  if (response.status === 404 || response.status === 405) throw new BackendUnavailableError();
  throw await toApiError(response);
}

export async function getJob(jobId: string): Promise<JobState> {
  if (isGoldenJobId(jobId)) {
    try {
      return await goldenJobState(jobId);
    } catch {
      throw new ApiError(404, 'not_found');
    }
  }
  let response: Response;
  try {
    response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  } catch {
    throw new ApiError(0, null);
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as JobState;
}

export async function retryCleanup(jobId: string, sku: string): Promise<void> {
  // A golden job is finished and clean; there is nothing to retry and nobody to ask.
  if (isGoldenJobId(jobId)) return;
  let response: Response;
  try {
    response = await fetch(
      `/api/jobs/${encodeURIComponent(jobId)}/products/${encodeURIComponent(sku)}/retry-cleanup`,
      { method: 'POST' },
    );
  } catch {
    throw new ApiError(0, null);
  }
  if (!response.ok) throw await toApiError(response);
}

export type ExportRequest = components['schemas']['ExportRequest'];
export type ExportResponse = components['schemas']['ExportResponse'];
export type ExportKind = components['schemas']['ExportKind'];

/**
 * Posts the reviewed products and the seller to the exporter. The client is the source of
 * truth after Review; the server only renders the files. A transport failure (offline, refused)
 * surfaces as `ApiError(0, null)`, distinct from a server refusal that carries a `code`.
 */
export async function exportJob(jobId: string, body: ExportRequest): Promise<ExportResponse> {
  // Demo Safe Mode: the bundled files are the export. No request, not even to the exporter.
  if (isGoldenJobId(jobId)) {
    try {
      return { job_id: jobId, files: await goldenExportFiles(jobId) };
    } catch {
      throw new ApiError(404, 'not_found');
    }
  }
  let response: Response;
  try {
    response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, null);
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as ExportResponse;
}

/** Poll cadence while a job runs. */
export const POLL_INTERVAL_MS = 2000;

export function isJobActive(job: JobState | undefined): boolean {
  return job === undefined || job.status === 'queued' || job.status === 'running';
}

/**
 * Polls `GET /api/jobs/{id}` every 2s while queued/running; stops on done/failed.
 * `null` disables the query: nothing is fetched until there is a job to ask about.
 */
export function useJob(jobId: string | null): UseQueryResult<JobState, ApiError> {
  return useQuery<JobState, ApiError>({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId ?? ''),
    enabled: jobId !== null,
    refetchInterval: (query) => (isJobActive(query.state.data) ? POLL_INTERVAL_MS : false),
    retry: (failureCount, error) => (error.status === 0 || error.status >= 500) && failureCount < 2,
  });
}
