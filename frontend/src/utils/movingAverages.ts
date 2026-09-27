import type { MovingAverageLine } from "../api/settings";
import type { Bar, IndicatorResult } from "../types/alpaca";
import { ema, sma } from "./chartStudies";

/**
 * The chart's three moving averages, computed from the candles on screen.
 *
 * The server draws its own defaults (app/indicators/ma.py) because the
 * chain has to carry a number for callers that are not this chart. What is
 * *drawn* is the reader's setting, so the lines are rebuilt here: the
 * lengths, the kind (EMA or SMA) and the colours come from Settings, and
 * the closes come from whatever timeframe is displayed. That keeps a 20 on
 * a 15m chart twenty 15m candles, and a change in Settings visible at
 * once, at every timeframe, without a round trip.
 *
 * A line that is switched off is left out entirely rather than drawn
 * empty, so the Levels menu and the chart legend only ever list lines the
 * reader asked for.
 */
export const MA_INDICATOR = "MA";

function lineName(line: MovingAverageLine): string {
  return `${line.type.toUpperCase()} ${line.length}`;
}

export function movingAverageSeries(bars: Bar[], lines: MovingAverageLine[]) {
  const closes = bars.map((b) => b.c);
  const series: IndicatorResult["series"] = {};
  const colors: Record<string, string> = {};
  const study: NonNullable<IndicatorResult["study"]> = {};
  const styles: NonNullable<IndicatorResult["styles"]> = {};
  lines.forEach((line) => {
    if (!line.enabled) return;
    // Two lines set to the same length and kind would collide on one name;
    // the second is simply the same line, so it is dropped.
    const name = lineName(line);
    if (series[name]) return;
    const values = line.type === "ema" ? ema(closes, line.length) : sma(closes, line.length);
    series[name] = bars.map((bar, i) => ({ t: bar.t, value: values[i] }));
    colors[name] = line.color;
    study[name] = { type: line.type, length: line.length };
    styles[name] = { width: line.width, dash: line.dash };
  });
  return { series, colors, study, styles };
}

/** Replaces the server's MA indicator with the reader's own lines. Other
 * indicators pass through untouched; so does the list itself when there is
 * no MA in it (the identity matters -- see aggregateBars). */
export function applyMovingAverages(
  indicators: IndicatorResult[],
  bars: Bar[],
  lines: MovingAverageLine[],
): IndicatorResult[] {
  if (!indicators.some((i) => i.name === MA_INDICATOR)) return indicators;
  const { series, colors, study, styles } = movingAverageSeries(bars, lines);
  return indicators.map((indicator) =>
    indicator.name === MA_INDICATOR ? { ...indicator, series, colors, study, styles } : indicator,
  );
}
