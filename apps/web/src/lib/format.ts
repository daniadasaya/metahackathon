/**
 * The one money formatter. Display is `Rp150.000`: integer rupiah, `Rp` glued to
 * the digits, `.` as the thousands separator, in both UI locales. Nothing else in the app formats
 * a rupiah amount, and nothing here computes one.
 */

/** Largest price the review field accepts: ten digits, far below `Number.MAX_SAFE_INTEGER`. */
export const MAX_PRICE_IDR = 9_999_999_999;

/** `150000` -> `Rp150.000`. */
export function formatRupiah(amount: number): string {
  const whole = String(Math.max(0, Math.trunc(amount)));
  return `Rp${whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;
}

/**
 * What the seller typed in the price field, reduced to an amount.
 * `null` means the field is empty (or only zeros): no price, and none is invented.
 * `undefined` means the input is over the limit and must be ignored.
 */
export function parseRupiahInput(text: string): number | null | undefined {
  const digits = text.replace(/\D/g, '').replace(/^0+/, '');
  if (digits === '') return null;
  const amount = Number(digits);
  return amount > MAX_PRICE_IDR ? undefined : amount;
}
