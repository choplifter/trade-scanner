import { useEffect, useMemo, useRef, useState } from "react";

import { referencePrice } from "../api/http";
import { getContractQuote } from "../api/options";
import type { Bar, ChartQuoteMessage, IndicatorResult } from "../types/alpaca";
import type { LegQuote } from "../types/options";
import { formatOcc, type ParsedOcc } from "../utils/occ";
import { FLIP_COLOR, negativeColor, positiveColor } from "./useGexLevels";
import { useChartPalette } from "./useSettings";

/** How often the underlying's price is re-read for the intrinsic line and
 * the expected-move band. */
const SPOT_POLL_MS = 5000;
/** How often the contract's greeks and IV are re-read; they move slowly. */
const GREEKS_POLL_MS = 30000;

const SESSION_COLOR = "#898781";
const QUOTE_COLOR = "#c08a2e";
const THETA_COLOR = "#b06fd6";
const EXPECTED_COLOR = "#5b8bd6";
const EMA_FAST_COLOR = "#2a9d8f";
const EMA_SLOW_COLOR = "#e76f51";

export const PREMIUM_LEVEL_NAMES = [
  "Quote",
  "Session",
  "Entry ±",
  "Intrinsic",
  "Expected move",
  "Theta",
  "EMA (premium)",
] as const;

/** The session (ET calendar day) a bar belongs to, as a sortable key. */
function sessionKey(bar: Bar): string {
  return new Date(bar.t).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function level(name: string, series: Record<string, number>, color: string | Record<string, string>): IndicatorResult {
  const colors: Record<string, string> = {};
  for (const key of Object.keys(series)) colors[key] = typeof color === "string" ? color : (color[key] ?? "#898781");
  return { name, kind: "level", series, colors, style: { width: 1, dash: "dashed" } } as IndicatorResult;
}

/** Milliseconds from now to today's 16:00 ET close (0 when already past). */
function msToClose(now: Date): number {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const close = new Date(et);
  close.setHours(16, 0, 0, 0);
  return Math.max(0, close.getTime() - et.getTime());
}

/** Years from now to the contract's expiry at the 16:00 ET close of that day. */
function yearsToExpiry(now: Date, expiry: string): number {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const [y, m, d] = expiry.split("-").map(Number);
  const exp = new Date(y, m - 1, d, 16, 0, 0, 0);
  return Math.max(0, exp.getTime() - et.getTime()) / (365 * 24 * 3600 * 1000);
}

function ema(values: number[], length: number): (number | null)[] {
  const k = 2 / (length + 1);
  const out: (number | null)[] = [];
  let prev: number | null = null;
  values.forEach((v, i) => {
    if (i < length - 1) {
      out.push(null);
      return;
    }
    if (prev == null) {
      prev = values.slice(i - length + 1, i + 1).reduce((a, b) => a + b, 0) / length;
    } else {
      prev = v * k + prev * (1 - k);
    }
    out.push(prev);
  });
  return out;
}

export interface PremiumLevels {
  /** Session-anchored VWAP of the premium, one entry per bar. */
  vwap: (number | null)[];
  /** Level and series indicators for the Levels menu. */
  levels: IndicatorResult[];
}

/** Levels that mean something on an option contract's own price axis --
 * everything the stock chart draws (daily range, GEX walls, EMAs of the
 * underlying) belongs to the underlying's axis and is left off the premium
 * chart. `contract` null means a stock chart: nothing is computed.
 * `intraday` gates the theta projection, whose points are minutes apart. */
export function usePremiumLevels(
  contract: ParsedOcc | null,
  bars: Bar[],
  quote: ChartQuoteMessage | null,
  entry: number | null,
  intraday: boolean,
): PremiumLevels {
  const [spot, setSpot] = useState<number | null>(null);
  const [greeks, setGreeks] = useState<LegQuote | null>(null);
  const { palette } = useChartPalette();
  const underlying = contract?.underlying ?? null;
  const symbol = contract ? formatOcc(contract) : null;

  // The underlying's last price, for the intrinsic value and the expected
  // move. Its own small poll: the stock stream only runs for symbols with
  // a chart open.
  useEffect(() => {
    setSpot(null);
    if (!underlying) return;
    let cancelled = false;
    const read = () => {
      if (document.hidden) return;
      referencePrice(underlying)
        .then((price) => {
          if (!cancelled) setSpot(price);
        })
        .catch(() => {});
    };
    read();
    const id = window.setInterval(read, SPOT_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [underlying]);

  // The contract's greeks and IV (Alpaca's snapshot; none close to expiry).
  useEffect(() => {
    setGreeks(null);
    if (!symbol) return;
    let cancelled = false;
    const read = () => {
      if (document.hidden) return;
      getContractQuote(symbol)
        .then((q) => {
          if (!cancelled) setGreeks(q);
        })
        .catch(() => {});
    };
    read();
    const id = window.setInterval(read, GREEKS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [symbol]);

  const vwap = useMemo<(number | null)[]>(() => {
    if (!contract) return [];
    const out: (number | null)[] = [];
    let session = "";
    let cumPV = 0;
    let cumV = 0;
    for (const bar of bars) {
      const key = sessionKey(bar);
      if (key !== session) {
        session = key;
        cumPV = 0;
        cumV = 0;
      }
      cumPV += ((bar.h + bar.l + bar.c) / 3) * bar.v;
      cumV += bar.v;
      out.push(cumV > 0 ? cumPV / cumV : null);
    }
    return out;
  }, [contract, bars]);

  const lastClose = bars.length > 0 ? bars[bars.length - 1].c : null;
  // Primitives rather than the quote object: a quote message arrives twice
  // a second even when bid and ask are unchanged.
  const bid = quote?.bid ?? null;
  const ask = quote?.ask ?? null;
  const mid = bid != null && ask != null ? Math.round(((bid + ask) / 2) * 100) / 100 : (greeks?.mid ?? lastClose);

  const computed = useMemo<IndicatorResult[]>(() => {
    if (!contract) return [];
    const out: IndicatorResult[] = [];

    if (bid != null || ask != null) {
      const series: Record<string, number> = {};
      if (bid != null && bid > 0) series.Bid = bid;
      if (ask != null && ask > 0) series.Ask = ask;
      if (Object.keys(series).length > 0) out.push(level("Quote", series, QUOTE_COLOR));
    }

    if (bars.length > 0) {
      const last = sessionKey(bars[bars.length - 1]);
      let high = -Infinity;
      let low = Infinity;
      let prevClose: number | null = null;
      for (const bar of bars) {
        const key = sessionKey(bar);
        if (key === last) {
          high = Math.max(high, bar.h);
          low = Math.min(low, bar.l);
        } else {
          prevClose = bar.c;
        }
      }
      const series: Record<string, number> = {};
      if (Number.isFinite(high)) series.High = high;
      if (Number.isFinite(low)) series.Low = low;
      if (prevClose != null) series["Prev close"] = prevClose;
      if (Object.keys(series).length > 0) out.push(level("Session", series, SESSION_COLOR));
    }

    if (entry != null && entry > 0) {
      out.push(
        level(
          "Entry ±",
          { "+100%": entry * 2, "+50%": entry * 1.5, "−50%": entry * 0.5 },
          { "+100%": positiveColor(), "+50%": positiveColor(), "−50%": negativeColor() },
        ),
      );
    }

    if (spot != null) {
      const intrinsic = contract.kind === "call" ? spot - contract.strike : contract.strike - spot;
      if (intrinsic > 0) out.push(level("Intrinsic", { Intrinsic: Math.round(intrinsic * 100) / 100 }, FLIP_COLOR));
    }

    // Expected move: where the premium lands if the underlying moves one
    // standard deviation (its implied vol over the shorter of "rest of
    // today" and "time to expiry") up or down, from delta and gamma --
    // a second-order estimate, not a repricing.
    if (spot != null && mid != null && greeks?.iv != null && greeks.delta != null) {
      const now = new Date();
      const horizonYears = Math.min(msToClose(now) / (365 * 24 * 3600 * 1000), yearsToExpiry(now, contract.expiry));
      if (horizonYears > 0) {
        const move = spot * greeks.iv * Math.sqrt(horizonYears);
        const gamma = greeks.gamma ?? 0;
        const up = mid + greeks.delta * move + 0.5 * gamma * move * move;
        const down = mid - greeks.delta * move + 0.5 * gamma * move * move;
        const series: Record<string, number> = {};
        series[`+1σ (${contract.underlying} ${(spot + move).toFixed(2)})`] = Math.max(0.01, Math.round(up * 100) / 100);
        series[`−1σ (${contract.underlying} ${(spot - move).toFixed(2)})`] = Math.max(0.01, Math.round(down * 100) / 100);
        out.push(level("Expected move", series, EXPECTED_COLOR));
      }
    }

    // Theta: where the premium would stand in an hour and at today's close
    // if the underlying did not move, from the snapshot's daily theta.
    // Levels rather than a projected line: points in the future would
    // extend the time scale and push the viewport off the newest bar.
    if (intraday && mid != null && greeks?.theta != null && greeks.theta < 0) {
      const remaining = msToClose(new Date());
      if (remaining > 0) {
        const day = 24 * 3600 * 1000;
        const series: Record<string, number> = {};
        const atClose = Math.max(0, Math.round((mid + greeks.theta * (remaining / day)) * 100) / 100);
        if (remaining > 3600 * 1000) {
          series["In 1h"] = Math.max(0, Math.round((mid + greeks.theta / 24) * 100) / 100);
        }
        series["At close"] = atClose;
        out.push(level("Theta", series, THETA_COLOR));
      }
    }
    return out;
    // palette: the entry-multiple colours are read at build time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contract, bars, bid, ask, entry, spot, greeks, mid, intraday, palette]);

  // `bars` gets a new identity on every trade tick, which would hand
  // CandleChart a new indicator list (and a rebuild of every line) several
  // times a second. Only a change in the values themselves goes out.
  const key = JSON.stringify(computed.map((i) => [i.name, i.series, i.colors]));
  const stableRef = useRef<{ key: string; value: IndicatorResult[] }>({ key: "", value: [] });
  if (stableRef.current.key !== key) stableRef.current = { key, value: computed };
  return { vwap, levels: stableRef.current.value };
}

/** EMA 9/20 of the premium, over the bars as displayed (already
 * aggregated to the chart's timeframe, so the series matches the candles
 * bar for bar). Null for a stock chart. */
export function usePremiumSeries(contract: ParsedOcc | null, displayedBars: Bar[]): IndicatorResult | null {
  return useMemo(() => {
    if (!contract || displayedBars.length < 9) return null;
    const closes = displayedBars.map((b) => b.c);
    const toPoints = (values: (number | null)[]) =>
      values.map((value, i) => ({ t: displayedBars[i].t, value })).filter((p) => p.value != null);
    return {
      name: "EMA (premium)",
      kind: "series",
      series: { "EMA 9": toPoints(ema(closes, 9)), "EMA 20": toPoints(ema(closes, 20)) },
      colors: { "EMA 9": EMA_FAST_COLOR, "EMA 20": EMA_SLOW_COLOR },
      style: { width: 1, dash: "solid" },
    } as IndicatorResult;
  }, [contract, displayedBars]);
}
