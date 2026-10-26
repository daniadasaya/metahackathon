/**
 * Mock job mode — a client-side simulated job so the whole Upload → Progress flow is drivable
 * before `POST /api/jobs` exists (live probe), and so Demo Safe Mode sample packs make zero API calls.
 *
 * Strictly gated: `api.ts` reaches for this only when the real route answers 404/405, or when
 * Demo Safe Mode is on and a sample pack was picked. Once the real route exists the real path
 * takes over with no change here.
 *
 * The job is a pure function of (job id, now): the id encodes the start time and photo count, so
 * a page reload resumes the same simulated run instead of losing it. The only memory is the set
 * of retries, which lives in this module for the session.
 */
import type { components } from './types';

type JobState = components['schemas']['JobState'];
type JobStage = components['schemas']['JobStage'];
type Product = components['schemas']['Product'];
type StageError = components['schemas']['StageError'];

const MOCK_PREFIX = 'mock-';

/** Milliseconds each stage lasts, in pipeline order. */
const STAGE_MS: ReadonlyArray<readonly [Exclude<JobStage, 'done'>, number]> = [
  ['concepts', 1500],
  ['segment', 2500],
  ['cleanup', 3000],
  ['extract', 2000],
];

const QUEUED_MS = 600;
const RETRY_MS = 2000;
const PRODUCTS_PER_PHOTO = 2;

/** `startedAt` in epoch ms, photo count; both recoverable from the id alone. */
export function makeMockJobId(photoCount: number, startedAt: number = Date.now()): string {
  return `${MOCK_PREFIX}${startedAt}-${Math.max(1, photoCount)}`;
}

export function isMockJobId(jobId: string): boolean {
  return /^mock-\d+-\d+$/.test(jobId);
}

function parseMockJobId(jobId: string): { startedAt: number; photos: number } | null {
  const match = /^mock-(\d+)-(\d+)$/.exec(jobId);
  if (match === null) return null;
  return { startedAt: Number(match[1]), photos: Number(match[2]) };
}

/** jobId -> epoch ms at which a retry of the failing photo started. */
const retries = new Map<string, number>();

/** The photo whose cleanup is made to fail on purpose: photo 2, or photo 1 if there is only one. */
function failingPhoto(photos: number): number {
  return Math.min(1, photos - 1);
}

interface Template {
  name: string;
  category: string;
  concept: string;
  descriptionId: string;
  descriptionEn: string;
  hint: number | null;
  suggestion: number | null;
}

// Product data, not UI copy: this stands in for what Stage E would write.
const TEMPLATES: readonly Template[] = [
  {
    name: 'Tas Batik Parang',
    category: 'Tas & Dompet',
    concept: 'batik tote bag',
    descriptionId: 'Tas batik motif parang, muat laptop 14 inci.',
    descriptionEn: 'Batik parang tote bag that fits a 14-inch laptop.',
    hint: 150000,
    suggestion: 165000,
  },
  {
    name: 'Dompet Kulit Coklat',
    category: 'Tas & Dompet',
    concept: 'leather wallet',
    descriptionId: 'Dompet kulit coklat, delapan slot kartu.',
    descriptionEn: 'Brown leather wallet with eight card slots.',
    hint: null,
    suggestion: 120000,
  },
  {
    name: 'Kaos Polos Putih',
    category: 'Pakaian',
    concept: 'white t-shirt',
    descriptionId: 'Kaos katun polos warna putih, ukuran L.',
    descriptionEn: 'Plain white cotton t-shirt, size L.',
    hint: 65000,
    suggestion: 70000,
  },
  {
    name: 'Keripik Singkong Pedas',
    category: 'Makanan Ringan',
    concept: 'cassava chips',
    descriptionId: 'Keripik singkong rasa pedas, kemasan 200 gram.',
    descriptionEn: 'Spicy cassava chips, 200 g pack.',
    hint: null,
    suggestion: null,
  },
];

function buildProduct(jobId: string, index: number, photos: number, retried: boolean): Product {
  const template = TEMPLATES[index % TEMPLATES.length] as Template;
  const photoIndex = Math.min(photos - 1, Math.floor(index / PRODUCTS_PER_PHOTO));
  const sku = `KK-${String(index + 1).padStart(4, '0')}`;
  const failed = photoIndex === failingPhoto(photos) && !retried;
  return {
    sku,
    job_id: jobId,
    source_photo_index: photoIndex,
    concept: template.concept,
    bbox: [40, 40, 240, 240],
    crop_url: `/p/${jobId}/${sku}-crop.jpg`,
    clean_url: `/p/${jobId}/${sku}.jpg`,
    // A failed edit falls back to the deterministic composite; a retry that passes the gate replaces it.
    cleanup_method: failed ? 'composite' : 'muse-image',
    fidelity: failed
      ? null
      : { same_product: true, color_match: true, logo_text_match: true, reason: 'Simulated.' },
    name: template.name,
    description_id: template.descriptionId,
    description_en: template.descriptionEn,
    category: template.category,
    brand: null,
    condition: 'new',
    attributes: { color: null, size: null, variant: null },
    price_hint_idr: template.hint,
    price_suggestion_idr: template.suggestion,
    confidence: 'medium',
    price_idr: null,
    price_via_chat: false,
    availability: 'in stock',
    included: true,
  };
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** The simulated polling payload for `jobId` at time `now`; `null` when the id is not a mock id. */
export function getMockJob(jobId: string, now: number = Date.now()): JobState | null {
  const parsed = parseMockJobId(jobId);
  if (parsed === null) return null;
  const { startedAt, photos } = parsed;
  const elapsed = now - startedAt - QUEUED_MS;
  const productsTotal = photos * PRODUCTS_PER_PHOTO;
  const iso = (ms: number): string => new Date(ms).toISOString();

  const base = { job_id: jobId, created_at: iso(startedAt), updated_at: iso(now) };

  if (elapsed < 0) {
    return {
      ...base,
      status: 'queued',
      stage: 'concepts',
      progress: { photos_total: photos, photos_done: 0, products_total: 0, products_cleaned: 0 },
      products: [],
      errors: [],
    };
  }

  let cursor = 0;
  let stage: JobStage = 'done';
  const bounds: Partial<Record<JobStage, [number, number]>> = {};
  for (const [name, duration] of STAGE_MS) {
    bounds[name] = [cursor, cursor + duration];
    if (stage === 'done' && elapsed < cursor + duration) stage = name;
    cursor += duration;
  }
  const finished = stage === 'done';

  const [segmentStart, segmentEnd] = bounds.segment ?? [0, 0];
  const [cleanupStart, cleanupEnd] = bounds.cleanup ?? [0, 0];
  const photosDone = finished
    ? photos
    : Math.floor(photos * clamp01((elapsed - segmentStart) / (segmentEnd - segmentStart)));

  const productsExist = elapsed >= cleanupEnd;
  const retryAt = retries.get(jobId);
  const retried = retryAt !== undefined && now >= retryAt + RETRY_MS;
  const failing = failingPhoto(photos);
  const products = productsExist
    ? Array.from({ length: productsTotal }, (_, index) => buildProduct(jobId, index, photos, retried))
    : [];

  const failedProducts = productsExist && !retried ? PRODUCTS_PER_PHOTO : 0;
  const cleanedCap = productsTotal - failedProducts;
  let productsCleaned = 0;
  if (elapsed >= cleanupStart) {
    productsCleaned = productsExist
      ? cleanedCap
      : Math.floor(cleanedCap * clamp01((elapsed - cleanupStart) / (cleanupEnd - cleanupStart)));
  }

  const errors: StageError[] = productsExist && !retried
    ? [{ photo_index: failing, stage: 'cleanup', code: 'cleanup_failed' }]
    : [];

  return {
    ...base,
    status: finished ? 'done' : 'running',
    stage,
    progress: {
      photos_total: photos,
      photos_done: photosDone,
      products_total: elapsed >= cleanupStart ? productsTotal : 0,
      products_cleaned: productsCleaned,
    },
    products,
    errors,
  };
}

/**
 * Re-run Stage D for the product `sku`. Resolves after the simulated delay; the job state clears the
 * error once the delay has passed. Rejects when the job or product is not part of the simulation.
 */
export function retryMockCleanup(jobId: string, sku: string): Promise<void> {
  const parsed = parseMockJobId(jobId);
  const match = /^KK-(\d{4})$/.exec(sku);
  if (parsed === null || match === null) return Promise.reject(new Error('not_a_mock_product'));
  const index = Number(match[1]) - 1;
  const photoIndex = Math.min(parsed.photos - 1, Math.floor(index / PRODUCTS_PER_PHOTO));
  if (photoIndex !== failingPhoto(parsed.photos)) return Promise.resolve();
  if (!retries.has(jobId)) retries.set(jobId, Date.now());
  return new Promise((resolve) => {
    window.setTimeout(resolve, RETRY_MS + 50);
  });
}
