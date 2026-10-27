#!/usr/bin/env node
/**
 * Builds the Demo Safe Mode golden bundles.
 *
 * For each sample pack it runs the real job endpoints of a running API, then freezes the result:
 *
 *   public/samples/{pack}/golden/
 *     products.json                       reviewed products, prices confirmed, URLs made static
 *     images/KK-0001.jpg, KK-0001-crop.jpg
 *     exports/catalog.csv, photos.zip, captions_instagram.txt, price_list_whatsapp.txt
 *
 * The four export files are the API exporter's own output (`POST /api/jobs/{id}/export`), so a
 * golden CSV and a live CSV cannot drift apart. Nothing here formats a price or builds a CSV.
 *
 * Usage (API must be up; META_MOCK=true is fine for a mock golden):
 *   node scripts/generate-goldens.js [--api http://127.0.0.1:8180] [--pack a,b,c]
 *
 * Price law: `price_idr` is the seller's confirmed value. It is the seller's own
 * `price_hint_idr` when the pipeline found one; otherwise it must be listed in CONFIRMED_PRICES,
 * a human decision made when the golden was reviewed. A product with neither makes the script
 * fail, and `price_suggestion_idr` is never used as a price.
 *
 * Known limitation: the CSV's `link` / `image_link` columns carry the generating API's `BASE_URL`
 * and job id, as every exporter output does. They are alive only while that API keeps the job.
 * Re-run against the deployment (with its `BASE_URL`) once the live golden run is accepted.
 */
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SAMPLES = path.join(ROOT, 'public', 'samples');

const SELLER = { store_name: 'Toko Contoh', wa_number: '6281234567890' };
const POLL_MS = 1000;
const POLL_LIMIT = 120;

/**
 * Prices a human confirmed while reviewing each golden. Keyed by SKU; `name` guards against the
 * pipeline's output drifting underneath the table (then the script stops instead of mispricing).
 * Pack A/B/C below were reviewed against the META_MOCK=true fixtures.
 */
const CONFIRMED_PRICES = {
  a: {
    'KK-0001': { name: 'HP Second - Smartphone', price: 1350000 },
    'KK-0002': { name: 'HP Second Warna Gelap', price: 1400000 },
    'KK-0003': { name: 'Smartphone Second Warna Gold', price: 1450000 },
    'KK-0007': { name: 'Kotak iPhones Hitam Segel Plastik', price: 75000 },
    'KK-0010': { name: 'Kotak Smartphone Hitam', price: 50000 },
    'KK-0012': { name: 'Casing Transparan For 14 Pro', price: 45000 },
    'KK-0013': { name: 'Charging Case Hitam', price: 150000 },
    'KK-0014': { name: 'Power Bank Putih', price: 185000 },
    'KK-0015': { name: 'Kabel Charging Putih', price: 35000 },
    'KK-0016': { name: 'Kabel Charging Second Putih dan Hitam', price: 50000 },
  },
  b: {
    'KK-0001': { name: 'Kaos Polos Katun Hitam', price: 85000 },
    'KK-0002': { name: 'Samsung Galaxy A54 8/128', price: 4500000 },
    'KK-0003': { name: 'Mie Instan Cup Ayam Bawang', price: 12000 },
    'KK-0005': { name: 'Cokelat Susu Batang 60g', price: 22000 },
    'KK-0006': { name: 'Kaos Polos Katun Hitam', price: 85000 },
  },
  c: {
    'KK-0001': { name: 'Cokelat Susu Batang 60g', price: 22000 },
    'KK-0002': { name: 'Samsung Galaxy A54 8/128', price: 4500000 },
    'KK-0003': { name: 'iPhone 11 64GB', price: 5200000 },
  },
};

/** Export kind -> file name inside `golden/exports/` (the names `src/lib/golden.ts` fetches). */
const EXPORT_FILES = {
  catalog_csv: 'catalog.csv',
  images_zip: 'photos.zip',
  captions_instagram: 'captions_instagram.txt',
  price_list_whatsapp: 'price_list_whatsapp.txt',
};

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : (process.argv[index + 1] ?? fallback);
}

const API = option('api', process.env.KK_API ?? 'http://127.0.0.1:8180').replace(/\/$/, '');
const PACKS = option('pack', 'a,b,c').split(',').map((pack) => pack.trim());
/** `--job <id>`: freeze an existing finished job instead of starting a new one. Single pack only. */
const JOB_OVERRIDE = option('job', '');
if (JOB_OVERRIDE !== '' && PACKS.length !== 1) throw new Error('--job needs exactly one --pack');

async function request(pathname, init) {
  const response = await fetch(`${API}${pathname}`, init);
  if (!response.ok) throw new Error(`${init?.method ?? 'GET'} ${pathname} -> HTTP ${response.status}`);
  return response;
}

async function startJob(pack) {
  const dir = path.join(SAMPLES, pack);
  const form = new FormData();
  for (const n of [1, 2]) {
    const bytes = await readFile(path.join(dir, `photo${n}.jpg`));
    form.append('photos', new Blob([bytes], { type: 'image/jpeg' }), `photo${n}.jpg`);
  }
  const general = (await readFile(path.join(dir, 'captions.txt'), 'utf8')).trim();
  form.append('captions', JSON.stringify({ general, photos: ['', ''] }));
  form.append('demo_safe', 'false');
  const state = await (await request('/api/jobs', { method: 'POST', body: form })).json();
  return state.job_id;
}

async function waitForJob(jobId) {
  for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
    const state = await (await request(`/api/jobs/${jobId}`)).json();
    if (state.status === 'done') return state;
    if (state.status === 'failed') throw new Error(`job ${jobId} failed`);
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  throw new Error(`job ${jobId} did not finish in ${(POLL_LIMIT * POLL_MS) / 1000}s`);
}

/** Confirmed price for one product, or a thrown error: nothing is ever inferred. */
function confirmedPrice(pack, product) {
  if (product.price_hint_idr !== null && product.price_hint_idr > 0) return product.price_hint_idr;
  const entry = CONFIRMED_PRICES[pack]?.[product.sku];
  if (entry === undefined) throw new Error(`pack ${pack} ${product.sku} (${product.name}): no confirmed price`);
  if (entry.name !== product.name) {
    throw new Error(`pack ${pack} ${product.sku}: reviewed "${entry.name}" but the pipeline now says "${product.name}"`);
  }
  return entry.price;
}

async function save(file, response) {
  await writeFile(file, Buffer.from(await response.arrayBuffer()));
}

async function buildPack(pack) {
  const golden = path.join(SAMPLES, pack, 'golden');
  await rm(golden, { recursive: true, force: true });
  await mkdir(path.join(golden, 'images'), { recursive: true });
  await mkdir(path.join(golden, 'exports'), { recursive: true });

  const jobId = JOB_OVERRIDE !== '' ? JOB_OVERRIDE : await startJob(pack);
  const state = await waitForJob(jobId);
  if (state.errors.length > 0) throw new Error(`pack ${pack}: job finished with ${state.errors.length} error(s)`);
  if (state.products.length === 0) throw new Error(`pack ${pack}: job produced no products`);

  const confirmed = state.products.map((product) => ({ ...product, price_idr: confirmedPrice(pack, product) }));

  // The exporter receives exactly what the client would post: no `price_suggestion_idr`.
  const reviewed = confirmed.map(({ price_suggestion_idr: _suggestion, ...rest }) => rest);
  const exported = await (
    await request(`/api/jobs/${jobId}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ products: reviewed, seller: SELLER }),
    })
  ).json();
  for (const file of exported.files) {
    const target = EXPORT_FILES[file.kind];
    if (target === undefined) throw new Error(`unknown export kind ${file.kind}`);
    await save(path.join(golden, 'exports', target), await request(new URL(file.url).pathname));
  }

  for (const product of confirmed) {
    await save(path.join(golden, 'images', `${product.sku}.jpg`), await request(`/p/${jobId}/${product.sku}.jpg`));
    await save(path.join(golden, 'images', `${product.sku}-crop.jpg`), await request(`/p/${jobId}/${product.sku}-crop.jpg`));
  }

  const base = `/samples/${pack}/golden/images`;
  const bundle = {
    source: process.env.KK_GOLDEN_SOURCE === 'live' ? 'live' : 'mock',
    generated_at: new Date().toISOString(),
    photos: state.progress.photos_total,
    seller: SELLER,
    // Unreviewed pipeline state: the seller confirms prices in Review. Only
    // `price_hint_idr` is kept, and the client pre-fills from it alone.
    products: state.products.map((product) => ({
      ...product,
      price_idr: null,
      price_via_chat: false,
      job_id: `golden-${pack}`,
      clean_url: `${base}/${product.sku}.jpg`,
      crop_url: `${base}/${product.sku}-crop.jpg`,
    })),
  };
  await writeFile(path.join(golden, 'products.json'), `${JSON.stringify(bundle, null, 2)}\n`);
  console.log(`pack ${pack}: ${confirmed.length} products, job ${jobId}, source ${bundle.source}`);
}

for (const pack of PACKS) {
  if (!(pack in CONFIRMED_PRICES)) throw new Error(`unknown pack "${pack}"`);
  await buildPack(pack);
}
