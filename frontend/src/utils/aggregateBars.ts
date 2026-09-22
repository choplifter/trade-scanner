import { isPointSeries } from "../types/alpaca";
import type { Bar, IndicatorResult } from "../types/alpaca";
import { computeStudy } from "./chartStudies";

export interface TimeframeOption {
  /** Stable id used as React key and selector state. */
  key: string;
  label: string;
  /**
   * "intraday": today's session, built by bucketing the live-updating
   * 1-minute feed client-side (see aggregateBars) -- carries the
   * session-anchored VWAP overlay.
   * "historical": fetched at native resolution from the backend over a
   * lookback long enough to fill the chart; no VWAP (it's not a
   * same-session concept once a bar spans more than one session), no live
   * updates.
   */
  kind: "intraday" | "historical";
  minutes?: number;
  alpacaTimeframe?: string;
}

export const TIMEFRAME_OPTIONS: TimeframeOption[] = [
  { key: "1m", label: "1m", kind: "intraday", minutes: 1 },
  { key: "5m", label: "5m", kind: "intraday", minutes: 5 },
  { key: "15m", label: "15m", kind: "intraday", minutes: 15 },
  { key: "1h", label: "1h", kind: "historical", alpacaTimeframe: "1Hour" },
  { key: "4h", label: "4h", kind: "historical", alpacaTimeframe: "4Hour" },
  { key: "1D", label: "D", kind: "historical", alpacaTimeframe: "1Day" },
  { key: "1W", label: "W", kind: "historical", alpacaTimeframe: "1Week" },
  { key: "1M", label: "M", kind: "historical", alpacaTimeframe: "1Month" },
];

/**
 * Alpaca's live stream only ever pushes closed 1-minute bars, so the
 * intraday timeframes are built by bucketing those 1-minute bars
 * client-side rather than requesting a different resolution from the
 * backend.
 */
/**
 * "series"-kind indicator sub-series (e.g. an EMA) carry the same per-bar
 * cardinality as `bars` -- both come from the same backend minute-bar
 * fetch -- so they need bucketing the same way vwap does (last known value
 * per bucket) whenever the chart itself is showing an aggregated timeframe.
 * Left unaggregated, a 1-minute-resolution line squeezed into a view built
 * for much coarser bars packs far more bars into the same visible time
 * window than the candles have, which trips lightweight-charts' own
 * minBarSpacing and silently narrows the visible range out from under the
 * user the moment the indicator is toggled on.
 */
export function aggregateBars(
  bars: Bar[],
  vwap: (number | null)[],
  minutes: number,
  indicators: IndicatorResult[] = [],
): { bars: Bar[]; vwap: (number | null)[]; indicators: IndicatorResult[] } {
  // Indicators that declare their formula (EMA, Bollinger, RSI -- see
  // utils/chartStudies) are recomputed from the candles below rather than
  // rebucketed: a 15m chart's RSI is fourteen 15m candles, not the last
  // minute value of a fourteen-*minute* RSI, and the backend's copy was
  // computed once at load while the candles keep arriving. That holds at 1m
  // too, so this can't return early there when one of them is on.
  const hasStudy = indicators.some((i) => i.study && Object.keys(i.study).length > 0);
  if (minutes === 1 && !hasStudy) return { bars, vwap, indicators };

  const bucketMs = minutes * 60_000;
  const outBars: Bar[] = minutes === 1 ? bars : [];
  const outVwap: (number | null)[] = minutes === 1 ? vwap : [];

  // Everything else point-valued is rebucketed, last known value per
  // bucket. Oscillators included: left at minute resolution they put a
  // minute's worth of timestamps between every candle, and the chart,
  // which lays its time axis over every series at once, spreads the candles
  // apart to make room for them.
  interface SeriesSlot {
    key: string;
    points: { t: string; value: number | null }[];
    out: (number | null)[];
  }
  const slots: SeriesSlot[] = [];
  const rebucketed = (indicator: IndicatorResult, subName: string) =>
    (indicator.kind === "series" || indicator.kind === "oscillator") && !indicator.study?.[subName];
  if (minutes > 1) {
    indicators.forEach((indicator, i) => {
      Object.entries(indicator.series).forEach(([subName, points]) => {
        if (rebucketed(indicator, subName) && isPointSeries(points)) {
          slots.push({ key: `${i}:${subName}`, points, out: [] });
        }
      });
    });

    for (let i = 0; i < bars.length; i++) {
      const bar = bars[i];
      const bucketStart = Math.floor(new Date(bar.t).getTime() / bucketMs) * bucketMs;
      const current = outBars[outBars.length - 1];

      if (current && new Date(current.t).getTime() === bucketStart) {
        current.h = Math.max(current.h, bar.h);
        current.l = Math.min(current.l, bar.l);
        current.c = bar.c;
        current.v += bar.v;
        outVwap[outVwap.length - 1] = vwap[i] ?? outVwap[outVwap.length - 1];
        slots.forEach((slot) => {
          const v = slot.points[i]?.value;
          slot.out[slot.out.length - 1] = v ?? slot.out[slot.out.length - 1];
        });
      } else {
        outBars.push({ t: new Date(bucketStart).toISOString(), o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v });
        outVwap.push(vwap[i] ?? null);
        slots.forEach((slot) => {
          slot.out.push(slot.points[i]?.value ?? null);
        });
      }
    }
  }

  // With nothing to rebucket or recompute, the caller's own array can go
  // back out unchanged. Identity matters here: CandleChart's indicators
  // effect tears down and rebuilds every price line whenever this reference
  // changes, and with trade ticks reshaping the forming candle several
  // times a second, a fresh array per call would have it doing that several
  // times a second too.
  if (slots.length === 0 && !hasStudy) return { bars: outBars, vwap: outVwap, indicators };

  const closes = outBars.map((b) => b.c);
  const slotsByKey = new Map(slots.map((s) => [s.key, s]));
  const outIndicators: IndicatorResult[] = indicators.map((indicator, i) => {
    if (indicator.kind !== "series" && indicator.kind !== "oscillator") return indicator;
    const newSeries: IndicatorResult["series"] = {};
    Object.keys(indicator.series).forEach((subName) => {
      const study = indicator.study?.[subName];
      if (study) {
        const values = computeStudy(study, closes);
        newSeries[subName] = outBars.map((b, idx) => ({ t: b.t, value: values[idx] }));
        return;
      }
      const slot = slotsByKey.get(`${i}:${subName}`);
      newSeries[subName] = slot
        ? outBars.map((b, idx) => ({ t: b.t, value: slot.out[idx] }))
        : indicator.series[subName];
    });
    return { ...indicator, series: newSeries };
  });

  return { bars: outBars, vwap: outVwap, indicators: outIndicators };
}
