/**
 * Centralized ILS (₪) formatting for the portal.
 *
 * M1 is mixed-unit: some endpoints return whole shekels as a float
 * (e.g. /book outstanding_amount, /summary income.*), others return
 * agorot as integer `*_minor_units` (e.g. /summary expenses/vat, /earnings).
 * Everything funnels through here so a ÷100 is never forgotten at a call site.
 */

const ILS0 = new Intl.NumberFormat("he-IL", {
  style: "currency",
  currency: "ILS",
  maximumFractionDigits: 0,
});

const ILS2 = new Intl.NumberFormat("he-IL", {
  style: "currency",
  currency: "ILS",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Convert a raw value to whole shekels (divides agorot when minorUnits). */
export function toMajorUnits(value: number | null | undefined, minorUnits = false): number {
  const v = value ?? 0;
  return minorUnits ? v / 100 : v;
}

/** Format a value as ₪. Pass { minorUnits: true } for agorot integers. */
export function formatILS(
  value: number | null | undefined,
  opts: { minorUnits?: boolean; decimals?: boolean } = {},
): string {
  const major = toMajorUnits(value, opts.minorUnits);
  return (opts.decimals ? ILS2 : ILS0).format(major);
}
