import { useEffect, useState } from "react";

import { getGex } from "../api/http";
import { getPalette } from "../api/settings";
import type { IndicatorResult } from "../types/alpaca";
import type { GexSymbolReading } from "../types/gex";

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
 * The current GEX reading for `symbol`, asked for by name so the backend
 * computes it if it has nothing cached -- any optionable ticker, not the
 * five that used to be precomputed. One-shot-fetch-on-symbol-change like
 * useSymbolInfo, plus a poll interval so a chart left on one symbol far
 * longer than the server's refresh cadence still picks up new readings.
 *
 * `loading` matters here in a way it did not before: a symbol the backend
 * has never computed costs a real chain fetch, so the first answer is
 * seconds away rather than milliseconds, and a consumer that renders
 * nothing while it waits looks broken. A poll refresh does not set it --
 * only the first fetch for a symbol, so a live chart doesn't flicker every
 * five minutes.
 *
 * Extracted out of useGexLevels so a consumer that wants the raw numbers
 * (e.g. a net-GEX readout) doesn't have to unpack them back out of an
 * IndicatorResult -- accepted tradeoff of a second independent poller per
 * consumer, same as every other small per-widget hook in this app (e.g.
 * useMarketConditions).
 */
export function useGexReading(symbol: string | null): {
  reading: GexSymbolReading | null;
  loading: boolean;
} {
  const [reading, setReading] = useState<GexSymbolReading | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!symbol) {
      setReading(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    // The previous symbol's walls are not this symbol's; clear them rather
    // than leaving them on the chart while the new ones are fetched.
    setReading(null);
    setLoading(true);
    const fetchGex = (first: boolean) => {
      getGex(symbol)
        .then((res) => {
          if (cancelled) return;
          setReading(res.symbols[symbol] ?? null);
          if (first) setLoading(false);
        })
        .catch(() => {
          if (!cancelled && first) setLoading(false);
        });
    };
    fetchGex(true);
    const interval = setInterval(() => fetchGex(false), REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol]);

  return { reading, loading };
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
  const { reading } = useGexReading(symbol);
  return gexLevelsFrom(reading);
}

/** The level set for a reading already in hand. Pure, so a consumer that
 * already calls useGexReading (for the net-GEX readout, say) builds its
 * levels from that one reading instead of running a second poller -- which
 * now that readings are fetched per symbol on demand would mean a second
 * round trip on every symbol change, not just a duplicated parse. */
export function gexLevelsFrom(reading: GexSymbolReading | null): IndicatorResult | null {
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
