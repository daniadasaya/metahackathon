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
/** Same marker `apps/api/app/exporter.py` reads (`MERGE_MARKER`). */
const MERGE_MARKER = '@merge:';
const POLL_MS = 1000;
const POLL_LIMIT = 120;

/**
 * Prices a human confirmed while reviewing each golden. Keyed by SKU; `name` guards against the
 * pipeline's output drifting underneath the table (then the script stops instead of mispricing).
 * Pack A was reviewed against the META_MOCK=true fixtures. Pack B and Pack C were reviewed against
 * the live jobs job-f6dce0c0ee01 and job-3d538290960c.
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
    'KK-0001': { name: 'Jaket Denim Thrift Pilihan - Kondisi Bagus Sudah Dicuci', price: 25000 },
    'KK-0002': { name: 'Kemeja Flanel Kotak-Kotak Merah Hitam Thrift - Obral Pilih Sendiri', price: 25000 },
    'KK-0003': { name: 'Hoodie Zipper Navy Thrift - Hangat, Bersih & Siap Pakai', price: 25000 },
    'KK-0004': { name: 'Kemeja Floral Thrift Motif Bunga Kondisi Bagus Sudah Dicuci', price: 25000 },
    'KK-0005': { name: 'Baju Thrift Pilihan Kondisi Bagus Sudah Dicuci', price: 25000 },
    'KK-0006': { name: 'Kaos Thrift Hitam Polos - Kondisi Bagus, Sudah Dicuci, Stok Terbatas', price: 25000 },
    'KK-0007': { name: 'Kaos Thrift Obral Bisa Pilih Sendiri - Kondisi Bagus Sudah Dicuci', price: 25000 },
    'KK-0008': { name: 'Kaos Thrift Pilihan Kondisi Bagus Sudah Dicuci', price: 25000 },
    'KK-0009': { name: 'Kemeja Denim Thrift Biru - Kondisi Bagus Sudah Dicuci', price: 35000 },
    'KK-0010': { name: 'Kaos Thrift Abu-abu Pilihan Kondisi Bagus Sudah Dicuci', price: 35000 },
    'KK-0011': { name: 'Kaos Thrift Garis Hitam Putih Kondisi Bagus Sudah Dicuci', price: 35000 },
    'KK-0012': { name: 'Kemeja Thrift Hijau Olive Kondisi Bagus - Sudah Dicuci Stok Terbatas', price: 35000 },
    'KK-0013': { name: 'Kaos Thrift Putih Pilihan Kondisi Bagus Sudah Dicuci', price: 35000 },
    'KK-0014': { name: 'Kaos Thrift Marun Pilihan Kondisi Bagus - Nyaman untuk Harian', price: 35000 },
    'KK-0015': { name: 'Baju Thrift Pilihan Navy Kondisi Bagus Sudah Dicuci', price: 35000 },
  },
  d: {
    'KK-0002': { name: 'Casing Silikon Premium untuk iPhone 14 Pro - Biru Navy', price: 45000 },
    'KK-0003': { name: 'Charger Dinding 33W Dual Port USB-A + USB-C Fast Charging - Putih', price: 85000 },
    'KK-0007': { name: 'Casing Charger TWS Earbud Putih Praktis dan Ringan', price: 165000 },
    'KK-0008': { name: 'Kabel Data & Charger USB-C Putih - Baru Praktis untuk HP', price: 35000 },
  },
  c: {
    'KK-0001': { name: 'Taro Net Seaweed Snack Rasa Rumput Laut', price: 9500 },
    'KK-0002': { name: 'Indomilk Susu UHT Strawberry Manis Segar', price: 6000 },
    'KK-0003': { name: 'Ultra Milk Susu UHT Coklat 200ml', price: 6500 },
    'KK-0004': { name: 'Snack Rumput Laut Renyah Gurih - Stok Baru', price: 13000 },
    'KK-0005': { name: 'Snack Seaweed Renyah Gurih - Stok Baru Warung', price: 10000 },
    'KK-0006': { name: 'Keripik Kentang Renyah Gurih - Stok Baru, Bisa Satuan atau Sekardus', price: 12000 },
    'KK-0007': { name: 'Roma Kelapa Biskuit Kelapa Renyah - Stok Baru, Bisa Satuan atau Kardus', price: 11000 },
    'KK-0008': { name: 'Keripik Chips Kentang Renyah - Stok Baru', price: 12000 },
    'KK-0009': { name: 'Beng-Beng Wafer Caramel Crispy Cokelat - Camilan Renyah', price: 3000 },
    'KK-0010': { name: 'Snack Rumput Laut Renyah Gurih - Stok Baru', price: 13000 },
    'KK-0011': { name: 'Snack Rumput Laut Renyah Gurih - Bisa Satuan atau Sekardus', price: 10000 },
    'KK-0012': { name: 'Snack Rumput Laut Renyah Gurih - Stok Baru Warung', price: 10000 },
    'KK-0013': { name: 'Snack Keripik Renyah Kemasan - Stok Baru Warung', price: 8000 },
    'KK-0014': { name: 'Wafer Camilan Renyah Stok Baru - Bisa Satuan atau Satu Kardus', price: 10000 },
    'KK-0015': { name: 'Snack Rumput Laut Renyah Gurih Siap Makan', price: 10000 },
    'KK-0016': { name: 'Kripik Chips Renyah Stok Baru - Bisa Satuan atau Sekardus', price: 10000 },
    'KK-0017': { name: 'Wafer Renyah Lapis Krim Kemasan Satuan - Stok Baru, Bisa Satuan atau Kardus', price: 10000 },
    'KK-0018': { name: 'Wafer Cokelat Koomuk Renyah - Camilan Manis Stok Baru', price: 3500 },
    'KK-0019': { name: 'KitKat Wafer Cokelat Renyah - Camilan Stok Baru', price: 6500 },
  },
};

/**
 * Multi-photo merges a human made while reviewing a golden. Each entry says that the `child`
 * product is another angle of the `parent` product. Matched by exact product NAME, never by SKU,
 * because SKU numbers depend on detection order. A name that matches zero or several products stops
 * the script. The child is excluded from the catalog and carries `@merge:{parentSku}` in its concept
 * (the wire convention `exporter.py` reads); its photo rides in the parent's additional images.
 */
const MERGES = {
  d: [
    {
      parent: 'Power Bank ROBOT 20000mAh 33W Fast Charging Hitam',
      child: 'Power Bank ROBOT 20000mAh RT32 Dual USB - Baru',
    },
    {
      parent: 'Casing Silikon Premium untuk iPhone 14 Pro - Biru Navy',
      child: 'Softcase Silikon Magnetic Premium Navy Elegan',
    },
    {
      parent: 'Casing Charger TWS Earbud Putih Praktis dan Ringan',
      child: 'Charging Case TWS Putih - Casing Pengisi Daya Earbud Baru',
    },
  ],
};

/**
 * SKUs a human excluded while reviewing the live run: the crop is a fragment of a product that
 * another product occludes in the source photo. They stay in products.json with `included: false`
 * (the seller's decision, visible in Review) and are never sent to the exporter.
 */
const REVIEW_EXCLUDED = {
  b: ['KK-0001', 'KK-0002', 'KK-0003', 'KK-0004', 'KK-0005', 'KK-0006', 'KK-0007', 'KK-0008'],
  c: ['KK-0002', 'KK-0004', 'KK-0005', 'KK-0006', 'KK-0007', 'KK-0008', 'KK-0010', 'KK-0011', 'KK-0012', 'KK-0015', 'KK-0016', 'KK-0017', 'KK-0019'],
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

/** The one product whose name is `name`; zero or several matches stop the script, nothing is guessed. */
function productByName(pack, products, name) {
  const matches = products.filter((product) => product.name === name);
  if (matches.length !== 1) {
    throw new Error(`pack ${pack}: merge name "${name}" matched ${matches.length} products, expected exactly 1`);
  }
  return matches[0];
}

/**
 * Resolves MERGES to a child-SKU -> parent-SKU map. A child may not be excluded in REVIEW_EXCLUDED,
 * may not be a parent itself, and its parent must be included, or the script throws.
 */
function resolveMerges(pack, products, excluded) {
  const pairs = (MERGES[pack] ?? []).map(({ parent, child }) => ({
    parent: productByName(pack, products, parent),
    child: productByName(pack, products, child),
  }));
  const children = new Set(pairs.map(({ child }) => child.sku));
  const merged = new Map();
  for (const { parent, child } of pairs) {
    if (child.sku === parent.sku) throw new Error(`pack ${pack}: ${child.sku} cannot merge into itself`);
    if (excluded.includes(child.sku)) throw new Error(`pack ${pack}: merged child ${child.sku} is also in REVIEW_EXCLUDED`);
    if (children.has(parent.sku)) throw new Error(`pack ${pack}: parent ${parent.sku} is itself a merged child`);
    if (!parent.included || excluded.includes(parent.sku)) {
      throw new Error(`pack ${pack}: merge parent ${parent.sku} (${parent.name}) is not included`);
    }
    merged.set(child.sku, parent.sku);
  }
  return merged;
}

/** A concept without any earlier `@merge:` suffix, so a re-merge never stacks markers. */
function baseConcept(concept) {
  const at = concept.indexOf(MERGE_MARKER);
  return at === -1 ? concept : concept.slice(0, at);
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

  const excluded = REVIEW_EXCLUDED[pack] ?? [];
  for (const sku of excluded) {
    if (!state.products.some((product) => product.sku === sku)) throw new Error(`pack ${pack}: excluded ${sku} is not in the job`);
  }
  // Merges come first: a merged child is never included, and it is never priced.
  const merged = resolveMerges(pack, state.products, excluded);
  // Included = the pipeline marked it, no reviewer excluded it, and it is not a merged child.
  // Only included products are priced.
  const confirmed = state.products.map((product) => {
    const parentSku = merged.get(product.sku);
    if (parentSku !== undefined) {
      const concept = `${baseConcept(product.concept)}${MERGE_MARKER}${parentSku}`;
      return { ...product, concept, included: false, price_idr: null };
    }
    const included = product.included && !excluded.includes(product.sku);
    return { ...product, included, price_idr: included ? confirmedPrice(pack, product) : null };
  });

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
    products: confirmed.map((product) => ({
      ...product,
      price_idr: null,
      price_via_chat: false,
      job_id: `golden-${pack}`,
      clean_url: `${base}/${product.sku}.jpg`,
      crop_url: `${base}/${product.sku}-crop.jpg`,
    })),
  };
  await writeFile(path.join(golden, 'products.json'), `${JSON.stringify(bundle, null, 2)}\n`);
  const kept = confirmed.filter((product) => product.included).length;
  console.log(`pack ${pack}: ${confirmed.length} products, job ${jobId}, source ${bundle.source}, ${kept} kept of ${confirmed.length} products`);
}

for (const pack of PACKS) {
  if (!(pack in CONFIRMED_PRICES)) throw new Error(`unknown pack "${pack}"`);
  await buildPack(pack);
}
