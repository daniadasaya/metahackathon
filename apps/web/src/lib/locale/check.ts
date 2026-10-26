/**
 * Compile-time locale checks. Type-only: emits nothing, runs nothing.
 *
 * `tsc --noEmit` (the first half of `npm run build`) fails here when
 *   (a) `id.ts` and `en.ts` do not have exactly the same key paths, or
 *   (b) the `errors` namespace of either file is not exactly one key per `ErrorCode`.
 *
 * The second one is why a new server error code cannot ship without a translation: the
 * regenerated `types.ts` grows the union and this file stops compiling until both
 * dictionaries are updated. Never silence a failure here; add the missing key.
 */
import type { components } from '../types';
import type { en } from './en';
import type { id } from './id';

type ErrorCode = components['schemas']['ErrorCode'];

/** True only when A and B are mutually identical (not merely assignable). */
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;

/** Compiles only when T is exactly `true`; the constraint is the assertion. */
type Expect<T extends true> = T;

/** Every leaf key path of a dictionary, dot-joined: `"progress.title"`. */
export type Paths<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${Prefix}${K}` : Paths<T[K], `${Prefix}${K}.`>;
}[keyof T & string];

export type LocaleChecks = [
  // (a) identical key sets, in both directions
  Expect<Equal<Paths<typeof id>, Paths<typeof en>>>,
  // (b) the errors namespace is exactly the ErrorCode enum — nothing missing, nothing extra
  Expect<Equal<keyof (typeof id)['errors'], ErrorCode>>,
  Expect<Equal<keyof (typeof en)['errors'], ErrorCode>>,
];
