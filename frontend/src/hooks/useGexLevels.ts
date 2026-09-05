import { useEffect, useState } from "react";

import { getGex } from "../api/http";
import { getPalette } from "../api/settings";
import type { IndicatorResult } from "../types/alpaca";
import type { GexSymbolReading, NearExpiryGex } from "../types/gex";

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
// The expected-move band is a range, not a side: one muted colour for both
// edges, distinct from the wall colours so the eye separates "where the
// market prices the day's reach" from "where dealers hedge".
export const EXPECTED_MOVE_COLOR = "#5f8f9a";

/** "0DTE" while today's expiry trades, else "1d", "3d" -- the tag every
 * near-expiry label carries so it cannot be mistaken for a 45-day wall. */
export function nearTag(near: NearExpiryGex): string {
  return near.is_today ? "0DTE" : `${near.dte}d`;
}

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


/**
 * The nearest expiry's walls and flip as their own level set, tagged with
 * the expiry ("0DTE Call Wall", "1d Flip") so they read apart from the
 * 45-day walls above. A separate IndicatorResult, not folded into "GEX":
 * the Levels checklist toggles by name, and one may want the day's walls
 * without the month's, or the reverse.
 */
export function nearGexLevelsFrom(reading: GexSymbolReading | null): IndicatorResult | null {
  const near = reading?.near;
  if (!near) return null;
  const tag = nearTag(near);
  const series: Record<string, number> = {};
  const colors: Record<string, string> = {};
  if (near.call_wall) {
    series[`${tag} Call Wall`] = near.call_wall.strike;
    colors[`${tag} Call Wall`] = positiveColor();
  }
  if (near.put_wall) {
    series[`${tag} Put Wall`] = near.put_wall.strike;
    colors[`${tag} Put Wall`] = negativeColor();
  }
  if (near.gamma_flip_strike != null) {
    series[`${tag} Flip`] = near.gamma_flip_strike;
    colors[`${tag} Flip`] = FLIP_COLOR;
  }
  if (Object.keys(series).length === 0) return null;
  return { name: "Near GEX", kind: "level", series, colors, style: { dash: "dashed" } };
}

/**
 * The expected-move band as two levels, spot +/- the ATM straddle to the
 * nearest expiry. Labelled with the expiry tag so "EM 0DTE +" is read as
 * the day's priced reach, and "EM 3d +" as the reach to Tuesday.
 */
export function expectedMoveLevelsFrom(reading: GexSymbolReading | null): IndicatorResult | null {
  const em = reading?.expected_move;
  if (!em) return null;
  const tag = em.dte === 0 ? "0DTE" : `${em.dte}d`;
  return {
    name: "EM band",
    kind: "level",
    // Keys start with the set's name so the chart label reads "EM band 3d +"
    // rather than "EM band EM 3d +" (CandleChart prefixes the name otherwise).
    series: { [`EM band ${tag} +`]: em.high, [`EM band ${tag} −`]: em.low },
    colors: { [`EM band ${tag} +`]: EXPECTED_MOVE_COLOR, [`EM band ${tag} −`]: EXPECTED_MOVE_COLOR },
    style: { dash: "sparse-dotted" },
  };
}
