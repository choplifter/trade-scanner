import type { Bar, IndicatorMarker, IndicatorResult } from "../types/alpaca";
import { isPointSeries } from "../types/alpaca";

/**
 * Markers for sharp moves in an outside series -- the 10-year yield under
 * an SPY chart -- judged candle by candle on the candles actually drawn.
 * The backend sends the series and the rule (DERIVE in
 * backend/app/indicators/loader.py, see ten_year_moves.py for the numbers)
 * rather than the markers, because what "sharp" means depends on the
 * candle: 2 bp is a big 15-minute move and an unremarkable day.
 */
export interface MoveRule {
  type: "moves";
  /** Prefix on each marker: "10Y +6bp". */
  label: string;
  unit: string;
  /** Series units -> marker units (percent -> basis points is 100). */
  per_unit: number;
  /** A typical day's move, in marker units, and how many of those
   * (square-root scaled to the candle) count as sharp. */
  daily_sigma: number;
  k: number;
  /** Never flag less than this, whatever the candle. */
  floor: number;
}

/** A regular session is 390 minutes; the square-root-of-time scaling runs
 * off that, so a 15m candle's threshold is sqrt(15/390) of a day's. */
const SESSION_MINUTES = 390;

export function moveThreshold(rule: MoveRule, candleMinutes: number): number {
  return Math.max(rule.floor, rule.k * rule.daily_sigma * Math.sqrt(candleMinutes / SESSION_MINUTES));
}

const ms = (t: string) => new Date(t).getTime();

/**
 * Per candle: the series' last value inside the candle against its last
 * value before the candle began -- the move over the candle, close to
 * close. A candle with no point inside it (the series was closed, or has
 * no history there) gets no marker, and does not reset what the next
 * candle is compared with.
 */
export function moveMarkers(
  bars: Bar[],
  points: { t: string; value: number | null }[],
  rule: MoveRule,
  candleMinutes: number,
): { rising: IndicatorMarker[]; falling: IndicatorMarker[] } {
  const rising: IndicatorMarker[] = [];
  const falling: IndicatorMarker[] = [];
  const threshold = moveThreshold(rule, candleMinutes);
  const series = points
    .filter((p): p is { t: string; value: number } => p.value != null)
    .map((p) => ({ at: ms(p.t), value: p.value }))
    .sort((a, b) => a.at - b.at);

  let j = 0;
  let before: number | null = null;
  for (let i = 0; i < bars.length; i++) {
    const start = ms(bars[i].t);
    const end = i + 1 < bars.length ? ms(bars[i + 1].t) : Infinity;
    // Everything before this candle is its "previous close".
    while (j < series.length && series[j].at < start) before = series[j++].value;
    let last: number | null = null;
    while (j < series.length && series[j].at < end) last = series[j++].value;
    if (last == null) continue;
    if (before != null) {
      const move = (last - before) * rule.per_unit;
      if (Math.abs(move) >= threshold) {
        const text = `${rule.label} ${move > 0 ? "+" : ""}${move.toFixed(0)}${rule.unit}`;
        const time = Math.floor(start / 1000);
        if (move > 0) rising.push({ time, position: "aboveBar", shape: "arrowUp", text });
        else falling.push({ time, position: "belowBar", shape: "arrowDown", text });
      }
    }
    before = last;
  }
  return { rising, falling };
}

/** Replaces every derive-rule indicator's point series with the markers it
 * implies for these candles. Everything else passes through untouched. */
export function deriveMarkers(indicators: IndicatorResult[], bars: Bar[], candleMinutes: number): IndicatorResult[] {
  if (!indicators.some((i) => i.derive)) return indicators;
  return indicators.map((indicator) => {
    const rule = indicator.derive;
    if (!rule || rule.type !== "moves") return indicator;
    const source = Object.values(indicator.series).find(isPointSeries);
    if (!source) return { ...indicator, series: {} };
    const { rising, falling } = moveMarkers(bars, source, rule, candleMinutes);
    return { ...indicator, series: { Rising: rising, Falling: falling } };
  });
}
