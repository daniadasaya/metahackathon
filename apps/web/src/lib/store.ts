/**
 * Client state. This module owns `localStorage`.
 *
 * Exactly six keys are ever persisted; a seventh is a defect. Locale and theme
 * are resolved here, in the browser, and are never fetched. The inline script in
 * `index.html` performs the same first-paint resolution before this module loads, so there is no
 * flash of the wrong theme.
 */
import { useSyncExternalStore } from 'react';

import { applyEdit, parseCatalog, type ProductEdit, type ReviewedProduct } from './catalog';
import type { components } from './types';

type SellerInfo = components['schemas']['SellerInfo'];

export type Theme = 'system' | 'light' | 'dark';
export type Locale = 'id' | 'en';

export const STORAGE_KEYS = {
  catalog: 'kk:catalog',
  job: 'kk:job',
  demoSafe: 'kk:demoSafe',
  seller: 'kk:seller',
  locale: 'kk:locale',
  theme: 'kk:theme',
} as const;

type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];

// ---------------------------------------------------------------------------
// Raw storage. When storage is unavailable (private mode, quota) the in-memory store below
// keeps the session working; persistence is lost, which is the honest degradation.
// ---------------------------------------------------------------------------

function readRaw(key: StorageKey): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(key: StorageKey, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Handled: state stays correct in memory for this session.
  }
}

// ---------------------------------------------------------------------------
// A tiny observable value, consumed through useSyncExternalStore.
// ---------------------------------------------------------------------------

type Listener = () => void;

interface ValueStore<T> {
  get: () => T;
  set: (next: T) => void;
  subscribe: (listener: Listener) => () => void;
}

function createValueStore<T>(initial: T): ValueStore<T> {
  let value = initial;
  const listeners = new Set<Listener>();
  return {
    get: () => value,
    set: (next) => {
      if (Object.is(next, value)) return;
      value = next;
      listeners.forEach((listener) => listener());
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

const THEME_ORDER: readonly Theme[] = ['system', 'light', 'dark'];

function parseTheme(raw: string | null): Theme {
  return raw === 'light' || raw === 'dark' ? raw : 'system';
}

function parseLocale(raw: string | null): Locale {
  return raw === 'en' ? 'en' : 'id';
}

const themeStore = createValueStore<Theme>(parseTheme(readRaw(STORAGE_KEYS.theme)));
const localeStore = createValueStore<Locale>(parseLocale(readRaw(STORAGE_KEYS.locale)));
const demoSafeStore = createValueStore<boolean>(readRaw(STORAGE_KEYS.demoSafe) === 'true');
const jobStore = createValueStore<string | null>(readRaw(STORAGE_KEYS.job));

const darkQuery: MediaQueryList = window.matchMedia('(prefers-color-scheme: dark)');

/** The concrete theme `data-theme` carries: `system` resolves through the OS preference. */
export function resolveTheme(theme: Theme): 'light' | 'dark' {
  if (theme === 'system') return darkQuery.matches ? 'dark' : 'light';
  return theme;
}

function applyTheme(): void {
  document.documentElement.dataset['theme'] = resolveTheme(themeStore.get());
}

function applyLocale(): void {
  document.documentElement.lang = localeStore.get();
}

/** system -> light -> dark -> system. */
export function nextTheme(theme: Theme): Theme {
  const index = THEME_ORDER.indexOf(theme);
  return THEME_ORDER[(index + 1) % THEME_ORDER.length] ?? 'system';
}

export function setTheme(theme: Theme): void {
  themeStore.set(theme);
  writeRaw(STORAGE_KEYS.theme, theme);
  applyTheme();
}

export function setLocale(locale: Locale): void {
  localeStore.set(locale);
  writeRaw(STORAGE_KEYS.locale, locale);
  applyLocale();
}

export function setDemoSafe(on: boolean): void {
  demoSafeStore.set(on);
  writeRaw(STORAGE_KEYS.demoSafe, on ? 'true' : 'false');
}

export function setStoredJobId(jobId: string | null): void {
  jobStore.set(jobId);
  writeRaw(STORAGE_KEYS.job, jobId);
}

export function getTheme(): Theme {
  return themeStore.get();
}

export function getLocale(): Locale {
  return localeStore.get();
}

export function getDemoSafe(): boolean {
  return demoSafeStore.get();
}

export function getStoredJobId(): string | null {
  return jobStore.get();
}

export function useTheme(): Theme {
  return useSyncExternalStore(themeStore.subscribe, themeStore.get);
}

export function useLocale(): Locale {
  return useSyncExternalStore(localeStore.subscribe, localeStore.get);
}

export function useDemoSafe(): boolean {
  return useSyncExternalStore(demoSafeStore.subscribe, demoSafeStore.get);
}

export function useStoredJobId(): string | null {
  return useSyncExternalStore(jobStore.subscribe, jobStore.get);
}

// `system` follows the OS live; other tabs stay in sync through `storage`.
darkQuery.addEventListener('change', () => {
  if (themeStore.get() === 'system') applyTheme();
});

window.addEventListener('storage', (event) => {
  if (event.key === STORAGE_KEYS.theme) {
    themeStore.set(parseTheme(event.newValue));
    applyTheme();
  } else if (event.key === STORAGE_KEYS.locale) {
    localeStore.set(parseLocale(event.newValue));
    applyLocale();
  } else if (event.key === STORAGE_KEYS.demoSafe) {
    demoSafeStore.set(event.newValue === 'true');
  } else if (event.key === STORAGE_KEYS.job) {
    jobStore.set(event.newValue);
  } else if (event.key === STORAGE_KEYS.catalog) {
    catalogStore.set(parseStoredCatalog(event.newValue));
  } else if (event.key === STORAGE_KEYS.seller) {
    sellerStore.set(parseStoredSeller(event.newValue));
  }
});

applyTheme();
applyLocale();

// ---------------------------------------------------------------------------
// Catalog (`kk:catalog`) and seller (`kk:seller`).
//
// The catalog is `ReviewedProduct[]`, a type with no `price_suggestion_idr`: the AI price guess
// has no field to live in, so it cannot be stored. State changes are visible to
// the UI at once; the write follows immediately for a tap and after 300ms of quiet for typing.
// ---------------------------------------------------------------------------

const TEXT_DEBOUNCE_MS = 300;

function parseJson(raw: string | null): unknown {
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function parseStoredCatalog(raw: string | null): ReviewedProduct[] | null {
  return parseCatalog(parseJson(raw));
}

function parseStoredSeller(raw: string | null): SellerInfo | null {
  const value = parseJson(raw);
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const storeName = record['store_name'];
  const waNumber = record['wa_number'];
  if (typeof storeName !== 'string' || typeof waNumber !== 'string') return null;
  return { store_name: storeName, wa_number: waNumber };
}

const catalogStore = createValueStore<ReviewedProduct[] | null>(
  parseStoredCatalog(readRaw(STORAGE_KEYS.catalog)),
);
const sellerStore = createValueStore<SellerInfo | null>(
  parseStoredSeller(readRaw(STORAGE_KEYS.seller)),
);

let catalogTimer: number | null = null;
let sellerTimer: number | null = null;

function persistCatalog(): void {
  if (catalogTimer !== null) {
    window.clearTimeout(catalogTimer);
    catalogTimer = null;
  }
  const catalog = catalogStore.get();
  writeRaw(STORAGE_KEYS.catalog, catalog === null ? null : JSON.stringify(catalog));
}

function persistSeller(): void {
  if (sellerTimer !== null) {
    window.clearTimeout(sellerTimer);
    sellerTimer = null;
  }
  const seller = sellerStore.get();
  writeRaw(STORAGE_KEYS.seller, seller === null ? null : JSON.stringify(seller));
}

/** A pending debounced write must not be lost when the page is hidden or closed. */
function flushPending(): void {
  if (catalogTimer !== null) persistCatalog();
  if (sellerTimer !== null) persistSeller();
}

window.addEventListener('pagehide', flushPending);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushPending();
});

export type WriteMode = 'now' | 'debounced';

export function getCatalog(): ReviewedProduct[] | null {
  return catalogStore.get();
}

export function useCatalog(): ReviewedProduct[] | null {
  return useSyncExternalStore(catalogStore.subscribe, catalogStore.get);
}

/** Replaces the whole catalog (hydration, server-field sync). Written immediately. */
export function replaceCatalog(products: ReviewedProduct[]): void {
  catalogStore.set(products);
  persistCatalog();
}

/**
 * Applies a seller edit to one product. `now` for a tap (select, toggle, chip): durable before the
 * finger leaves the screen. `debounced` for a text field: 300ms after the last keystroke.
 */
export function editProduct(sku: string, edit: ProductEdit, mode: WriteMode): void {
  const current = catalogStore.get();
  if (current === null) return;
  catalogStore.set(current.map((product) => (product.sku === sku ? applyEdit(product, edit) : product)));
  if (mode === 'now') {
    persistCatalog();
    return;
  }
  if (catalogTimer !== null) window.clearTimeout(catalogTimer);
  catalogTimer = window.setTimeout(persistCatalog, TEXT_DEBOUNCE_MS);
}

export function getSeller(): SellerInfo | null {
  return sellerStore.get();
}

export function useSeller(): SellerInfo | null {
  return useSyncExternalStore(sellerStore.subscribe, sellerStore.get);
}

/** The shop name, typed in a text field. The WhatsApp number is the pipeline's and is carried through as is. */
export function setStoreName(storeName: string): void {
  const current = sellerStore.get();
  sellerStore.set({ store_name: storeName, wa_number: current?.wa_number ?? '' });
  if (sellerTimer !== null) window.clearTimeout(sellerTimer);
  sellerTimer = window.setTimeout(persistSeller, TEXT_DEBOUNCE_MS);
}

