/** Diverging color-mix weight for a heatmap tile: 0 = neutral (--surface),
 * 1 = fully saturated toward --delta-up/--delta-down. Capped below 1 so
 * even the most extreme mover keeps a hint of the surface tone rather than
 * a flat, oversaturated block -- see components.md's fill guidance. */
const MAX_BLEND = 0.85;

/** Above this weight, tile text switches to white -- see marks-and-anatomy
 * on picking label ink by the fill's luminance, not the page's light/dark
 * mode (the fill itself is what's saturated, in either mode). */
export const HEATMAP_STRONG_THRESHOLD = 0.4;

export function heatmapBlendWeight(pctChange: number, maxAbsPct: number): number {
  if (maxAbsPct <= 0) return 0;
  return Math.min(Math.abs(pctChange) / maxAbsPct, 1) * MAX_BLEND;
}

export function heatmapFill(pctChange: number, maxAbsPct: number): string {
  const weight = heatmapBlendWeight(pctChange, maxAbsPct);
  const hueVar = pctChange >= 0 ? "var(--delta-up)" : "var(--delta-down)";
  return `color-mix(in oklab, ${hueVar} ${(weight * 100).toFixed(1)}%, var(--surface))`;
}
