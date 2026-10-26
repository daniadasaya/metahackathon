/**
 * Pure catalog logic for the Review screen: no React, no storage, no strings.
 *
 * The Price Law lives in the types here. `kk:catalog` holds `ReviewedProduct[]`, a type
 * with no `price_suggestion_idr` field, and `toReviewed` builds one field by field, so an AI price
 * guess cannot be carried into storage (or from there into an export) by a spread or a cast.
 */
import { MAX_PRICE_IDR } from './format';
import type { components } from './types';

type Schemas = components['schemas'];

export type Product = Schemas['Product'];
export type ReviewedProduct = Schemas['ReviewedProduct'];

/** The only fields the seller edits on a card; everything else is read-only. */
export type ProductEdit = Partial<
  Pick<ReviewedProduct, 'name' | 'price_idr' | 'price_via_chat' | 'availability' | 'included'>
>;

/** A price is a positive whole number of rupiah. Zero, negatives and fractions mean "no price". */
export function positiveOrNull(value: number | null): number | null {
  return value !== null && Number.isSafeInteger(value) && value > 0 && value <= MAX_PRICE_IDR
    ? value
    : null;
}

/**
 * A server `Product` as the client keeps it after Review. Built field by field: the suggestion is
 * left out by construction (a missing field is a compile error, an extra one an excess-property
 * error). `price_idr` is seeded from the seller's own `price_hint_idr` and from nothing else.
 */
export function toReviewed(product: Product): ReviewedProduct {
  return {
    sku: product.sku,
    job_id: product.job_id,
    source_photo_index: product.source_photo_index,
    concept: product.concept,
    bbox: product.bbox,
    crop_url: product.crop_url,
    clean_url: product.clean_url,
    cleanup_method: product.cleanup_method,
    fidelity: product.fidelity,
    name: product.name,
    description_id: product.description_id,
    description_en: product.description_en,
    category: product.category,
    brand: product.brand,
    condition: product.condition,
    attributes: product.attributes,
    price_hint_idr: product.price_hint_idr,
    confidence: product.confidence,
    price_idr: positiveOrNull(product.price_idr ?? product.price_hint_idr),
    price_via_chat: product.price_via_chat,
    availability: product.availability,
    included: product.included,
  };
}

export function hydrateCatalog(products: readonly Product[]): ReviewedProduct[] {
  return products.map(toReviewed);
}

/** Applies a seller edit. The price is normalised here, so no caller can store `0` as a price. */
export function applyEdit(product: ReviewedProduct, edit: ProductEdit): ReviewedProduct {
  const next: ReviewedProduct = { ...product, ...edit };
  if (edit.price_idr !== undefined) next.price_idr = positiveOrNull(edit.price_idr);
  return next;
}

function sameFidelity(a: Product['fidelity'], b: Product['fidelity']): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * After a Stage D retry the server owns a new photo and gate verdict for a product the seller may
 * already have edited. Refreshes exactly those three server-owned fields and leaves every seller
 * edit alone. Returns `null` when nothing changed, so callers can skip the write.
 */
export function syncServerFields(
  stored: readonly ReviewedProduct[],
  fresh: readonly Product[],
): ReviewedProduct[] | null {
  const bySku = new Map(fresh.map((product) => [product.sku, product]));
  let changed = false;
  const next = stored.map((item) => {
    const latest = bySku.get(item.sku);
    if (latest === undefined || latest.job_id !== item.job_id) return item;
    if (
      latest.clean_url === item.clean_url &&
      latest.cleanup_method === item.cleanup_method &&
      sameFidelity(latest.fidelity, item.fidelity)
    ) {
      return item;
    }
    changed = true;
    return {
      ...item,
      clean_url: latest.clean_url,
      cleanup_method: latest.cleanup_method,
      fidelity: latest.fidelity,
    };
  });
  return changed ? next : null;
}

// ---------------------------------------------------------------------------
// Readiness
// ---------------------------------------------------------------------------

/** Ready to export: the seller confirmed a price, or chose to settle it over chat. */
export function hasPrice(product: ReviewedProduct): boolean {
  return product.price_via_chat || positiveOrNull(product.price_idr) !== null;
}

export interface Readiness {
  /** Included products that are ready. */
  ready: number;
  /** Included products with no confirmed price and not via chat: they block export. */
  blockers: ReviewedProduct[];
  /** Products the seller left out of the catalog. */
  excluded: number;
  /** Included products, ready or not. */
  included: number;
}

export function readiness(products: readonly ReviewedProduct[]): Readiness {
  const included = products.filter((product) => product.included);
  const blockers = included.filter((product) => !hasPrice(product));
  return {
    ready: included.length - blockers.length,
    blockers,
    excluded: products.length - included.length,
    included: included.length,
  };
}

/** Export needs at least one included product and no unpriced included product. */
export function canExport(state: Readiness): boolean {
  return state.included > 0 && state.blockers.length === 0;
}

export type GateState = 'pass' | 'fallback';

/**
 * `pass` only for a generative edit with a fully positive fidelity verdict. Anything else, including
 * an edit with no verdict on record, is shown as the standard photo: the UI never claims a pass it
 * cannot show evidence for.
 */
export function gateState(product: ReviewedProduct): GateState {
  const verdict = product.fidelity;
  const passed =
    product.cleanup_method === 'muse-image' &&
    verdict !== null &&
    verdict.same_product &&
    verdict.color_match &&
    verdict.logo_text_match;
  return passed ? 'pass' : 'fallback';
}

// ---------------------------------------------------------------------------
// Reading `kk:catalog` back. Storage is untrusted input: validate, then rebuild field by field so
// a stray key (a leaked `price_suggestion_idr`, say) is dropped rather than carried along.
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function strOrNull(value: unknown): string | null | undefined {
  return value === null ? null : str(value);
}

function bool(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function wholeNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function wholeNumberOrNull(value: unknown): number | null | undefined {
  return value === null ? null : wholeNumber(value);
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return allowed.find((candidate) => candidate === value);
}

function parseBbox(value: unknown): ReviewedProduct['bbox'] | undefined {
  if (!Array.isArray(value) || value.length !== 4) return undefined;
  const [a, b, c, d] = value as unknown[];
  return typeof a === 'number' && typeof b === 'number' && typeof c === 'number' && typeof d === 'number'
    ? [a, b, c, d]
    : undefined;
}

function parseAttributes(value: unknown): ReviewedProduct['attributes'] | undefined {
  if (!isRecord(value)) return undefined;
  const color = strOrNull(value['color']);
  const size = strOrNull(value['size']);
  const variant = strOrNull(value['variant']);
  return color === undefined || size === undefined || variant === undefined
    ? undefined
    : { color, size, variant };
}

function parseFidelity(value: unknown): ReviewedProduct['fidelity'] | undefined {
  if (value === null) return null;
  if (!isRecord(value)) return undefined;
  const sameProduct = bool(value['same_product']);
  const colorMatch = bool(value['color_match']);
  const logoTextMatch = bool(value['logo_text_match']);
  const reason = str(value['reason']);
  return sameProduct === undefined ||
    colorMatch === undefined ||
    logoTextMatch === undefined ||
    reason === undefined
    ? undefined
    : {
        same_product: sameProduct,
        color_match: colorMatch,
        logo_text_match: logoTextMatch,
        reason,
      };
}

/** One stored entry, or `null` when it is not a complete `ReviewedProduct`. */
export function parseReviewed(value: unknown): ReviewedProduct | null {
  if (!isRecord(value)) return null;

  const sku = str(value['sku']);
  const jobId = str(value['job_id']);
  const sourcePhotoIndex = wholeNumber(value['source_photo_index']);
  const concept = str(value['concept']);
  const bbox = parseBbox(value['bbox']);
  const cropUrl = str(value['crop_url']);
  const cleanUrl = str(value['clean_url']);
  const cleanupMethod = oneOf(value['cleanup_method'], ['muse-image', 'composite'] as const);
  const fidelity = parseFidelity(value['fidelity']);
  const name = str(value['name']);
  const descriptionId = str(value['description_id']);
  const descriptionEn = str(value['description_en']);
  const category = str(value['category']);
  const brand = strOrNull(value['brand']);
  const condition = oneOf(value['condition'], ['new', 'used'] as const);
  const attributes = parseAttributes(value['attributes']);
  const priceHint = wholeNumberOrNull(value['price_hint_idr']);
  const confidence = oneOf(value['confidence'], ['high', 'medium', 'low'] as const);
  const price = wholeNumberOrNull(value['price_idr']);
  const viaChat = bool(value['price_via_chat']);
  const availability = oneOf(value['availability'], ['in stock', 'out of stock'] as const);
  const included = bool(value['included']);

  if (
    sku === undefined ||
    jobId === undefined ||
    sourcePhotoIndex === undefined ||
    concept === undefined ||
    bbox === undefined ||
    cropUrl === undefined ||
    cleanUrl === undefined ||
    cleanupMethod === undefined ||
    fidelity === undefined ||
    name === undefined ||
    descriptionId === undefined ||
    descriptionEn === undefined ||
    category === undefined ||
    brand === undefined ||
    condition === undefined ||
    attributes === undefined ||
    priceHint === undefined ||
    confidence === undefined ||
    price === undefined ||
    viaChat === undefined ||
    availability === undefined ||
    included === undefined
  ) {
    return null;
  }

  return {
    sku,
    job_id: jobId,
    source_photo_index: sourcePhotoIndex,
    concept,
    bbox,
    crop_url: cropUrl,
    clean_url: cleanUrl,
    cleanup_method: cleanupMethod,
    fidelity,
    name,
    description_id: descriptionId,
    description_en: descriptionEn,
    category,
    brand,
    condition,
    attributes,
    price_hint_idr: priceHint,
    confidence,
    price_idr: positiveOrNull(price),
    price_via_chat: viaChat,
    availability,
    included,
  };
}

/** The whole stored catalog, or `null` when any entry is unusable (the screen then re-hydrates). */
export function parseCatalog(value: unknown): ReviewedProduct[] | null {
  if (!Array.isArray(value)) return null;
  const products: ReviewedProduct[] = [];
  for (const entry of value as unknown[]) {
    const product = parseReviewed(entry);
    if (product === null) return null;
    products.push(product);
  }
  return products;
}

// ---------------------------------------------------------------------------
// Export scope
// ---------------------------------------------------------------------------

export interface ExportScope {
  jobId: string;
  /** Every product of that one job, left-out ones included; the exporter filters on `included`. */
  products: ReviewedProduct[];
  /** True when the catalog also holds products from another job, which are not exported. */
  mixed: boolean;
}

/**
 * The one job an export covers. The export route is per job, so a catalog that somehow holds
 * several is narrowed to the current run (`kk:job`) or, failing that, the last job in the list.
 */
export function exportScope(
  catalog: readonly ReviewedProduct[] | null,
  currentJobId: string | null,
): ExportScope | null {
  if (catalog === null || catalog.length === 0) return null;
  const jobIds = new Set(catalog.map((product) => product.job_id));
  const last = catalog[catalog.length - 1];
  const jobId = currentJobId !== null && jobIds.has(currentJobId) ? currentJobId : last?.job_id;
  if (jobId === undefined) return null;
  return {
    jobId,
    products: catalog.filter((product) => product.job_id === jobId),
    mixed: jobIds.size > 1,
  };
}

/** Digits only, international, no `+` (mirrors the `SellerInfo` contract). */
export function isWaNumber(value: string): boolean {
  return /^\d+$/.test(value);
}
