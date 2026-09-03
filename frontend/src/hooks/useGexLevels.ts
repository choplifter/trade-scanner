import { useEffect, useState } from "react";

import { getGex } from "../api/http";
import { getPalette } from "../api/settings";
import type { IndicatorResult } from "../types/alpaca";
import type { GexSymbolReading } from "../types/gex";

// Mirrors backend app.market_data.gamma_exposure.SYMBOLS -- kept as one
// list here rather than duplicating the check in both ChartWidget and
// GexPlanWidget, so the two can't drift apart.
const GEX_SYMBOLS = new Set(["SPY", "QQQ", "TSLA", "NVDA", "PLTR"]);

export function isGexSymbol(symbol: string | null): symbol is string {
  return symbol != null && GEX_SYMBOLS.has(symbol);
}

const REFRESH_MS = 5 * 60_000; // matches backend's gex_refresh_interval (300s)
// Matches the light-theme hex values CandleChart's own position lines use
// (POSITION_TARGET_COLOR/POSITION_STOP_COLOR) -- this chart's price-line
// colors aren't theme-reactive anywhere today, so this doesn't introduce a
// new pattern.
/** The scheme's up/down colours (Settings dialog), read at call time. */
export function positiveColor(): string {
  return getPalette().up;
}
export function negativeColor(): string {
  return getPalette().down;
}
// Neither support nor resistance, so it gets its own neutral color rather
// than borrowing the positive/negative convention above.
export const FLIP_COLOR = "#8a6fd6";

/**
 * The current GEX reading for `symbol` (only computed for a fixed symbol
 * list -- see backend app.market_data.gamma_exposure.SYMBOLS, and
 * isGexSymbol below), or `null` for any other symbol. Fetches the whole
 * /api/meta/gex response (every covered symbol) and picks out `symbol`'s
 * row -- one-shot-fetch-on-symbol-change like useSymbolInfo, plus a poll
 * interval so a chart that stays mounted on a covered symbol far longer than
 * the server's refresh cadence still picks up new readings.
 *
 * Extracted out of useGexLevels so a consumer that wants the raw numbers
 * (e.g. a net-GEX readout) doesn't have to unpack them back out of an
 * IndicatorResult -- accepted tradeoff of a second independent poller per
 * consumer, same as every other small per-widget hook in this app (e.g.
 * useMarketConditions).
 */
export function useGexReading(symbol: string | null): GexSymbolReading | null {
  const [reading, setReading] = useState<GexSymbolReading | null>(null);

  useEffect(() => {
    if (!symbol) {
      setReading(null);
      return;
    }

    let cancelled = false;
    const fetchGex = () => {
      getGex()
        .then((res) => {
          if (!cancelled) setReading(res.symbols[symbol] ?? null);
        })
        .catch(() => {});
    };
    fetchGex();
    const interval = setInterval(fetchGex, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol]);

  return reading;
}

/**
 * `symbol`'s GEX reading as a "level"-kind IndicatorResult ready to splice
 * into CandleChart's `indicators` prop alongside its normal bar-computed
 * indicators: the top-5 gamma walls, the call wall and put wall called out
 * by name, and the approximate gamma-flip strike -- see backend's
 * top_walls()/call_wall()/put_wall()/gamma_flip_strike().
 *
 * Keyed by strike so a wall that's also the call/put wall (common -- the
 * biggest wall by |net_gex| is often the biggest on one side too) draws once
 * with the more specific label, not twice at the same price.
 */
export function useGexLevels(symbol: string | null): IndicatorResult | null {
  const reading = useGexReading(symbol);
  if (!reading) return null;

  const series: Record<string, number> = {};
  const colors: Record<string, string> = {};
  const setLevel = (label: string, strike: number, color: string) => {
    series[label] = strike;
    colors[label] = color;
  };

  for (const wall of reading.top_walls) {
    setLevel(`Wall ${wall.strike}`, wall.strike, wall.net_gex >= 0 ? positiveColor() : negativeColor());
  }
  // Drawn after the generic walls so a coinciding strike keeps this more
  // specific label (see setLevel dedup note above -- same `series` key).
  const dropCoincidingWall = (strike: number) => {
    for (const label of Object.keys(series)) {
      if (series[label] === strike) {
        delete series[label];
        delete colors[label];
      }
    }
  };
  if (reading.call_wall) {
    dropCoincidingWall(reading.call_wall.strike);
    setLevel("Call Wall", reading.call_wall.strike, positiveColor());
  }
  if (reading.put_wall) {
    dropCoincidingWall(reading.put_wall.strike);
    setLevel("Put Wall", reading.put_wall.strike, negativeColor());
  }
  if (reading.gamma_flip_strike != null) {
    setLevel("Flip", reading.gamma_flip_strike, FLIP_COLOR);
  }

  if (Object.keys(series).length === 0) return null;
  return { name: "GEX", kind: "level", series, colors };
}
