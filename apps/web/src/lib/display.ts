/**
 * Display helpers that are not money. Money has exactly one formatter, `lib/format.ts`,
 * and nothing here formats a rupiah amount.
 */

/** Zero-based photo index -> the 1-based number a seller counts by. */
export function photoNumber(photoIndex: number): number {
  return photoIndex + 1;
}

/** Stage letter shown on the strip: concepts=A … done=E. */
export const STAGE_LETTERS = ['A', 'B', 'C', 'D', 'E'] as const;
