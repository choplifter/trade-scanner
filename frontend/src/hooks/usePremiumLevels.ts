import { useEffect, useMemo, useState } from "react";

import { referencePrice } from "../api/http";
import type { Bar, ChartQuoteMessage, IndicatorResult } from "../types/alpaca";
import type { ParsedOcc } from "../utils/occ";
import { FLIP_COLOR, NEGATIVE_COLOR, POSITIVE_COLOR } from "./useGexLevels";

/** How often the underlying's price is re-read for the intrinsic line. */
const SPOT_POLL_MS = 5000;

const SESSION_COLOR = "#898781";
const QUOTE_COLOR = "#c08a2e";

export const PREMIUM_LEVEL_NAMES = ["Quote", "Session", "Entry ±", "Intrinsic"] as const;

/** The session (ET calendar day) a bar belongs to, as a sortable key. */
function sessionKey(bar: Bar): string {
  return new Date(bar.t).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function level(name: string, series: Record<string, number>, color: string | Record<string, string>): IndicatorResult {
  const colors: Record<string, string> = {};
  for (const key of Object.keys(series)) colors[key] = typeof color === "string" ? color : (color[key] ?? "#898781");
  return { name, kind: "level", series, colors, style: { width: 1, dash: "dashed" } } as IndicatorResult;
}

export interface PremiumLevels {
  /** Session-anchored VWAP of the premium, one entry per bar. */
  vwap: (number | null)[];
  /** Level indicators for the Levels menu: bid/ask, session high/low and
   * previous close, entry multiples, intrinsic value. */
  levels: IndicatorResult[];
}

/** Levels that mean something on an option contract's own price axis --
 * everything the stock chart draws (daily range, GEX walls, EMAs of the
 * underlying) belongs to the underlying's axis and is left off the premium
 * chart. `contract` null means a stock chart: nothing is computed. */
export function usePremiumLevels(
  contract: ParsedOcc | null,
  bars: Bar[],
  quote: ChartQuoteMessage | null,
  entry: number | null,
): PremiumLevels {
  const [spot, setSpot] = useState<number | null>(null);
  const underlying = contract?.underlying ?? null;

  // The underlying's last price, for the intrinsic value. Its own small
  // poll: the stock stream only runs for symbols with a chart open.
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

  const levels = useMemo<IndicatorResult[]>(() => {
    if (!contract) return [];
    const out: IndicatorResult[] = [];

    if (quote && (quote.bid != null || quote.ask != null)) {
      const series: Record<string, number> = {};
      if (quote.bid != null && quote.bid > 0) series.Bid = quote.bid;
      if (quote.ask != null && quote.ask > 0) series.Ask = quote.ask;
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
          { "+100%": POSITIVE_COLOR, "+50%": POSITIVE_COLOR, "−50%": NEGATIVE_COLOR },
        ),
      );
    }

    if (spot != null) {
      const intrinsic = contract.kind === "call" ? spot - contract.strike : contract.strike - spot;
      if (intrinsic > 0) out.push(level("Intrinsic", { Intrinsic: Math.round(intrinsic * 100) / 100 }, FLIP_COLOR));
    }
    return out;
  }, [contract, bars, quote, entry, spot]);

  return { vwap, levels };
}
