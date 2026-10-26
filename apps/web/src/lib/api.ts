/**
 * The one API client. Relative paths only (`/api/...`, `/p/...`): no base URL, no env var, no
 * origin. The server's `message` is developer English and is never read here beyond parsing the
 * envelope; only `code` leaves this module.
 *
 * Mock job mode (`mockJob.ts`) sits strictly behind two conditions: the real route answered
 * 404/405 because it does not exist yet (live probe), or Demo Safe Mode is on and a sample pack was
 * chosen (product invariant: zero API calls). A real upload with a live route never touches it.
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { getMockJob, isMockJobId, makeMockJobId, retryMockCleanup } from './mockJob';
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

/** The route is not implemented on this server (stage owns it), as opposed to a real refusal. */
function isMissingRoute(error: ApiError): boolean {
  return error.status === 404 || error.status === 405;
}

export interface CreateJobInput {
  photos: File[];
  /** One caption per photo, same order as `photos`; empty string when none. */
  photoCaptions: string[];
  generalCaption: string;
  demoSafe: boolean;
  samplePack: SamplePack | null;
}

export interface CreateJobResult {
  jobId: string;
  /** True when the job is client-side simulated rather than created on the server. */
  simulated: boolean;
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
    const response = await fetch(`/samples/${pack}/photo${n}.jpg`);
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

export async function createJob(input: CreateJobInput): Promise<CreateJobResult> {
  if (input.demoSafe && input.samplePack !== null) {
    return { jobId: makeMockJobId(SAMPLE_PACK_PHOTOS), simulated: true };
  }

  const form = new FormData();
  input.photos.forEach((photo) => form.append('photos', photo, photo.name));
  form.append(
    'captions',
    JSON.stringify({ general: input.generalCaption, photos: input.photoCaptions }),
  );
  form.append('demo_safe', String(input.demoSafe));

  const response = await fetch('/api/jobs', { method: 'POST', body: form });
  if (response.ok) {
    const state = (await response.json()) as JobState;
    return { jobId: state.job_id, simulated: false };
  }

  const error = await toApiError(response);
  if (isMissingRoute(error)) {
    return { jobId: makeMockJobId(Math.max(1, input.photos.length)), simulated: true };
  }
  throw error;
}

export async function getJob(jobId: string): Promise<JobState> {
  if (isMockJobId(jobId)) {
    const mock = getMockJob(jobId);
    if (mock !== null) return mock;
  }
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as JobState;
}

export async function retryCleanup(jobId: string, sku: string): Promise<void> {
  if (isMockJobId(jobId)) {
    await retryMockCleanup(jobId, sku);
    return;
  }
  const response = await fetch(
    `/api/jobs/${encodeURIComponent(jobId)}/products/${encodeURIComponent(sku)}/retry-cleanup`,
    { method: 'POST' },
  );
  if (!response.ok) throw await toApiError(response);
}

/** Poll cadence while a job runs. */
export const POLL_INTERVAL_MS = 2000;

export function isJobActive(job: JobState | undefined): boolean {
  return job === undefined || job.status === 'queued' || job.status === 'running';
}

/** Polls `GET /api/jobs/{id}` every 2s while queued/running; stops on done/failed. */
export function useJob(jobId: string): UseQueryResult<JobState, ApiError> {
  return useQuery<JobState, ApiError>({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (query) => (isJobActive(query.state.data) ? POLL_INTERVAL_MS : false),
    retry: (failureCount, error) => error.status >= 500 && failureCount < 2,
  });
}
