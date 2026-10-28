/**
 * Demo Safe Mode transport. A sample pack picked while the toggle is on is served
 * from the static golden bundle under `/samples/{pack}/golden/`: the products, the images and the
 * four export files. Nothing here calls `/api/...`; the only requests are for static files of the
 * same origin. Downloads are written to a Blob, never requested from the exporter.
 *
 * The bundle is produced by `scripts/generate-goldens.js` through the same exporter the live path
 * uses, so a golden CSV and a live CSV cannot drift apart.
 *
 * A golden job has a fixed id, `golden-{pack}`, so a reload resumes it and the stored catalog
 * (`kk:catalog`) keeps whatever the seller has edited since.
 */
import type { SamplePack } from './api';
import type { components } from './types';

type JobState = components['schemas']['JobState'];
type Product = components['schemas']['Product'];
type SellerInfo = components['schemas']['SellerInfo'];
type ExportFile = components['schemas']['ExportFile'];
type ExportKind = components['schemas']['ExportKind'];

/** `products.json` as `scripts/generate-goldens.js` writes it. */
interface GoldenBundle {
  /** Where the bundled run came from: the mock adapter or the live Meta API. */
  source: 'mock' | 'live';
  generated_at: string;
  photos: number;
  seller: SellerInfo;
  products: Product[];
}

const GOLDEN_JOB = /^golden-([abcd])$/;

export function goldenJobId(pack: SamplePack): string {
  return `golden-${pack}`;
}

export function isGoldenJobId(jobId: string): boolean {
  return GOLDEN_JOB.test(jobId);
}

export function packOfGoldenJobId(jobId: string): SamplePack | null {
  const match = GOLDEN_JOB.exec(jobId);
  return match === null ? null : (match[1] as SamplePack);
}

/**
 * Fetches a static file. The SPA catch-all answers a missing file with the HTML shell and status
 * 200, so a successful status alone proves nothing; the content type has to be the right kind.
 */
async function fetchAsset(path: string, wanted: (type: string) => boolean): Promise<Response> {
  const response = await fetch(path);
  const type = response.headers.get('content-type') ?? '';
  if (!response.ok || !wanted(type)) throw new Error(`golden asset unavailable: ${path}`);
  return response;
}

const notHtml = (type: string): boolean => !type.startsWith('text/html');

async function readBundle(pack: SamplePack): Promise<GoldenBundle> {
  const response = await fetchAsset(`/samples/${pack}/golden/products.json`, (type) => type.includes('json'));
  const bundle = (await response.json()) as GoldenBundle;
  if (!Array.isArray(bundle.products) || bundle.products.length === 0) {
    throw new Error(`golden bundle ${pack} holds no products`);
  }
  return bundle;
}

const bundles = new Map<SamplePack, Promise<GoldenBundle>>();

/** Loads (once) the golden bundle of a pack. A failed load is not remembered, so a retry can succeed. */
function loadBundle(pack: SamplePack): Promise<GoldenBundle> {
  const cached = bundles.get(pack);
  if (cached !== undefined) return cached;
  const pending = readBundle(pack);
  bundles.set(pack, pending);
  pending.catch(() => bundles.delete(pack));
  return pending;
}

/** Warms the bundle so the job can start. Rejects when the pack ships no golden. */
export async function loadGolden(pack: SamplePack): Promise<void> {
  await loadBundle(pack);
}

/** The finished job a golden pack stands for. Immediate: there is no pipeline to wait for. */
export async function goldenJobState(jobId: string): Promise<JobState> {
  const pack = packOfGoldenJobId(jobId);
  if (pack === null) throw new Error(`not a golden job id: ${jobId}`);
  const bundle = await loadBundle(pack);
  return {
    job_id: jobId,
    status: 'done',
    stage: 'done',
    progress: {
      photos_total: bundle.photos,
      photos_done: bundle.photos,
      products_total: bundle.products.length,
      products_cleaned: bundle.products.length,
    },
    products: bundle.products,
    errors: [],
    created_at: bundle.generated_at,
    updated_at: bundle.generated_at,
  };
}

const EXPORT_ASSETS: ReadonlyArray<{
  kind: ExportKind;
  asset: string;
  mime: string;
  filename: (pack: SamplePack) => string;
}> = [
  { kind: 'catalog_csv', asset: 'catalog.csv', mime: 'text/csv;charset=utf-8', filename: () => 'katalog-meta.csv' },
  { kind: 'images_zip', asset: 'photos.zip', mime: 'application/zip', filename: () => 'katalog-kilat-images.zip' },
  {
    kind: 'captions_instagram',
    asset: 'captions_instagram.txt',
    mime: 'text/plain;charset=utf-8',
    filename: (pack) => `katalog-golden-${pack}-captions.txt`,
  },
  {
    kind: 'price_list_whatsapp',
    asset: 'price_list_whatsapp.txt',
    mime: 'text/plain;charset=utf-8',
    filename: (pack) => `katalog-golden-${pack}-whatsapp.txt`,
  },
];

const exportFiles = new Map<SamplePack, Promise<ExportFile[]>>();

async function readExportFiles(pack: SamplePack): Promise<ExportFile[]> {
  return Promise.all(
    EXPORT_ASSETS.map(async ({ kind, asset, mime, filename }) => {
      const response = await fetchAsset(`/samples/${pack}/golden/exports/${asset}`, notHtml);
      const blob = new Blob([await response.arrayBuffer()], { type: mime });
      return { kind, filename: filename(pack), url: URL.createObjectURL(blob) };
    }),
  );
}

/** The four bundled export files as Blob URLs, ready for `<a download>`. Built once per pack. */
export function goldenExportFiles(jobId: string): Promise<ExportFile[]> {
  const pack = packOfGoldenJobId(jobId);
  if (pack === null) return Promise.reject(new Error(`not a golden job id: ${jobId}`));
  const cached = exportFiles.get(pack);
  if (cached !== undefined) return cached;
  const pending = readExportFiles(pack);
  exportFiles.set(pack, pending);
  pending.catch(() => exportFiles.delete(pack));
  return pending;
}
