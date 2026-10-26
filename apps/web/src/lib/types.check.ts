/**
 * Compile-time contract check for the generated API types.
 *
 * Type-only: this file emits nothing and runs nothing. `tsc --noEmit` (part of `npm run build`)
 * fails here when `types.ts` — regenerated from `apps/api/openapi.json` — loses or reshapes a
 * field the web app depends on. When it fails after a deliberate backend change, update the
 * assertion in the same commit; never silence it.
 */
import type { components } from './types';

type S = components['schemas'];

/** True only when A and B are mutually identical (not merely assignable). */
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;

/** Compiles only when T is exactly `true`; the constraint is the assertion. */
type Expect<T extends true> = T;

/** Logical negation of a boolean literal type. */
type Not<T extends boolean> = T extends true ? false : true;

/** True when K is a known key of T. For `never` or an index signature this is also true, so it cannot hide a loss. */
type HasKey<T, K extends string> = K extends keyof T ? true : false;

/** Element type of an array type; `never` when T is not an array. */
type Elem<T> = T extends readonly (infer E)[] ? E : never;

/** True only when no property of T is optional (every §10 field is present on the wire). */
type AllRequired<T> = Equal<Required<T>, T>;

/** Number of members in a string-literal union, via union -> intersection of functions -> tuple. */
type UnionToIntersection<U> =
  (U extends unknown ? (arg: U) => void : never) extends (arg: infer I) => void ? I : never;
type LastOf<U> =
  UnionToIntersection<U extends unknown ? () => U : never> extends () => infer L ? L : never;
type UnionToTuple<U, Acc extends unknown[] = []> = [U] extends [never]
  ? Acc
  : UnionToTuple<Exclude<U, LastOf<U>>, [LastOf<U>, ...Acc]>;
type CountOf<U> = UnionToTuple<U> extends infer T extends unknown[] ? T['length'] : never;

type Product = S['Product'];
type Reviewed = S['ReviewedProduct'];

export type ContractChecks = [
  // 0. Required-ness: no §10 field is optional, and the property counts are pinned.
  Expect<AllRequired<Product>>,
  Expect<Equal<CountOf<keyof Product>, 23>>,
  Expect<AllRequired<Reviewed>>,
  Expect<Equal<CountOf<keyof Reviewed>, 22>>,
  Expect<AllRequired<S['ProductAttributes']>>,
  Expect<Equal<CountOf<keyof S['ProductAttributes']>, 3>>,
  Expect<AllRequired<S['ProductEntry']>>,
  Expect<Equal<CountOf<keyof S['ProductEntry']>, 10>>,

  // 1. Product: identity and price fields. Nullability is separate from optionality: `null`, never `undefined`.
  Expect<Equal<Product['sku'], string>>,
  Expect<Equal<Product['brand'], string | null>>,
  Expect<Equal<Product['price_hint_idr'], number | null>>,
  Expect<Equal<Product['price_suggestion_idr'], number | null>>,
  Expect<Equal<Product['price_idr'], number | null>>,
  Expect<Equal<Product['price_via_chat'], boolean>>,
  Expect<Equal<Product['description_id'], string>>,
  Expect<Equal<Product['description_en'], string>>,
  Expect<Equal<Product['cleanup_method'], S['CleanupMethod']>>,
  Expect<Equal<Product['fidelity'], S['FidelityVerdict'] | null>>,
  Expect<Equal<Product['attributes'], S['ProductAttributes']>>,
  Expect<Equal<Product['confidence'], S['Confidence']>>,
  Expect<Equal<Product['included'], boolean>>,
  Expect<HasKey<Product, 'price_suggestion_idr'>>,

  // 2. ReviewedProduct: the suggestion must be structurally absent; the seller's price stays.
  Expect<Not<HasKey<Reviewed, 'price_suggestion_idr'>>>,
  Expect<HasKey<Reviewed, 'price_idr'>>,
  Expect<HasKey<Reviewed, 'price_hint_idr'>>,
  Expect<Equal<Reviewed['price_idr'], number | null>>,
  Expect<Equal<Reviewed['price_via_chat'], boolean>>,
  Expect<Equal<Reviewed['sku'], string>>,

  // 3. ErrorCode: exact membership and a pinned count. A new member fails here until updated on purpose.
  Expect<'not_found' extends S['ErrorCode'] ? true : false>,
  Expect<'price_missing' extends S['ErrorCode'] ? true : false>,
  Expect<'internal_error' extends S['ErrorCode'] ? true : false>,
  Expect<Equal<CountOf<S['ErrorCode']>, 14>>,
  Expect<
    Equal<
      S['ErrorCode'],
      | 'not_found'
      | 'invalid_request'
      | 'unsupported_file_type'
      | 'file_too_large'
      | 'model_not_found'
      | 'model_unavailable'
      | 'concepts_failed'
      | 'segmentation_failed'
      | 'no_products_found'
      | 'cleanup_failed'
      | 'extraction_failed'
      | 'price_missing'
      | 'export_failed'
      | 'internal_error'
    >
  >,

  // 4. Job enums are closed.
  Expect<Equal<S['JobStatus'], 'queued' | 'running' | 'done' | 'failed'>>,
  Expect<Equal<S['JobStage'], 'concepts' | 'segment' | 'cleanup' | 'extract' | 'done'>>,

  // 5. JobState: products are Product[]; errors never carry an English `message`.
  Expect<Equal<S['JobState']['products'], Product[]>>,
  Expect<Equal<Elem<S['JobState']['errors']>, S['StageError']>>,
  Expect<Not<HasKey<Elem<S['JobState']['errors']>, 'message'>>>,
  Expect<Equal<S['StageError']['code'], S['ErrorCode']>>,

  // 6. ExportRequest carries ReviewedProduct[] and a seller.
  Expect<Equal<S['ExportRequest']['products'], Reviewed[]>>,
  Expect<Equal<S['ExportRequest']['seller'], S['SellerInfo']>>,

  // 7. Wire enums used by the exporter.
  Expect<Equal<S['Availability'], 'in stock' | 'out of stock'>>,
  Expect<Equal<S['Condition'], 'new' | 'used'>>,
  Expect<Equal<S['CleanupMethod'], 'muse-image' | 'composite'>>,
];
