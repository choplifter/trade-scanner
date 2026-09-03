/** Display-only OCC parsing (SPY251219C00650000 -> SPY 19 Dec 650C). The
 * backend is the source of truth for grouping; this just labels legs. */

export interface ParsedOcc {
  root: string;
  underlying: string;
  expiry: string; // YYYY-MM-DD
  kind: "call" | "put";
  strike: number;
}

const OCC = /^([A-Z0-9]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export function parseOcc(symbol: string): ParsedOcc | null {
  const m = OCC.exec(symbol.trim().toUpperCase());
  if (!m) return null;
  const [, root, yy, mm, dd, cp, strike] = m;
  return {
    root,
    underlying: root.replace(/\d+$/, "") || root,
    expiry: `20${yy}-${mm}-${dd}`,
    kind: cp === "C" ? "call" : "put",
    strike: Number(strike) / 1000,
  };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatExpiry(expiry: string): string {
  const [, m, d] = expiry.split("-");
  const month = MONTHS[Number(m) - 1] ?? m;
  return `${Number(d)} ${month}`;
}

export function formatStrike(strike: number): string {
  return Number.isInteger(strike) ? String(strike) : strike.toFixed(2).replace(/0$/, "");
}

/** "SPY 19 Dec 650C" -- or the raw symbol when it does not parse. */
export function formatLeg(symbol: string): string {
  const parsed = parseOcc(symbol);
  if (!parsed) return symbol;
  return `${parsed.underlying} ${formatExpiry(parsed.expiry)} ${formatStrike(parsed.strike)}${parsed.kind === "call" ? "C" : "P"}`;
}

/** What the chart should load for a symbol: an option contract's
 * underlying (the bars endpoint only knows stocks), anything else as is. */
export function chartSymbolOf(symbol: string): string {
  return parseOcc(symbol)?.underlying ?? symbol;
}
