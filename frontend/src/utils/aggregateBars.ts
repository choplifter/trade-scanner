import type { Bar } from "../types/alpaca";

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
export function aggregateBars(
  bars: Bar[],
  vwap: (number | null)[],
  minutes: number,
): { bars: Bar[]; vwap: (number | null)[] } {
  if (minutes === 1) return { bars, vwap };

  const bucketMs = minutes * 60_000;
  const outBars: Bar[] = [];
  const outVwap: (number | null)[] = [];

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
    } else {
      outBars.push({ t: new Date(bucketStart).toISOString(), o: bar.o, h: bar.h, l: bar.l, c: bar.c, v: bar.v });
      outVwap.push(vwap[i] ?? null);
    }
  }

  return { bars: outBars, vwap: outVwap };
}
