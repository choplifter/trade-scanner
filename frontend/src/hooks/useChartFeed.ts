import { useEffect, useState } from "react";

import { getSymbolBars } from "../api/http";
import { chartSocket } from "../api/ws";
import type { Bar, IndicatorResult } from "../types/alpaca";
import { useStrategySettingsNonce } from "./useStrategySettingsNonce";

export interface ChartFeedState {
  bars: Bar[];
  /** Anchored at the 09:30 open -- the day-trading convention. */
  vwap: (number | null)[];
  /** Anchored at the premarket open, counting every print -- what
   * TradingView draws. Carried alongside rather than instead: one shared
   * accumulator per symbol serves viewers on either anchor only if both
   * travel together and the choice is made client-side. */
  vwapPremarket: (number | null)[];
  /** From the initial REST fetch only -- not refreshed per live WS tick,
   * since these (premarket/weekly/monthly range) don't change bar to bar. */
  indicators: IndicatorResult[];
  error: string | null;
  loading: boolean;
}

function sameMinute(a: string, b: string): boolean {
  return Date.parse(a) === Date.parse(b);
}

const EMPTY_STATE: ChartFeedState = {
  bars: [],
  vwap: [],
  vwapPremarket: [],
  indicators: [],
  error: null,
  loading: false,
};

/**
 * Seeds from today's REST bar history, then appends each live 1-minute bar
 * as it arrives over the socket -- so `bars`/`vwap` always hold the full,
 * ordered 1-minute sequence for the session, which is what timeframe
 * aggregation (see aggregateBars) needs as its input.
 */
export function useChartFeed(symbol: string | null): ChartFeedState {
  const [state, setState] = useState<ChartFeedState>(EMPTY_STATE);
  // Signal settings (opening-range length, strategy switches) shape the
  // indicators the REST fetch returns; a change re-runs the whole effect.
  // The brief ws re-subscribe that rides along is harmless -- the next tick
  // repopulates it.
  const settingsNonce = useStrategySettingsNonce();

  useEffect(() => {
    if (!symbol) {
      setState(EMPTY_STATE);
      return;
    }

    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });

    getSymbolBars(symbol)
      .then((res) => {
        if (!cancelled) {
          setState((s) => ({
            ...s,
            bars: res.bars,
            vwap: res.vwap,
            vwapPremarket: res.vwap_premarket ?? [],
            indicators: res.indicators,
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState((s) => ({ ...s, error: String(err), loading: false }));
        }
      });

    const unsubscribe = chartSocket.subscribe(symbol, (msg) => {
      if (msg.type === "bar") {
        setState((s) => {
          const bars = [...s.bars];
          const vwap = [...s.vwap];
          const vwapPremarket = [...s.vwapPremarket];
          const last = bars[bars.length - 1];
          // Compared as instants, not strings: the last bar may be one this
          // hook synthesised from trades, whose timestamp is formatted by
          // the trade path rather than the bar path. A string mismatch
          // would append a second bar for the same minute, which
          // lightweight-charts rejects as out-of-order data.
          if (last && sameMinute(last.t, msg.bar.t)) {
            bars[bars.length - 1] = msg.bar;
            vwap[vwap.length - 1] = msg.vwap;
            vwapPremarket[vwapPremarket.length - 1] = msg.vwap_premarket ?? null;
          } else {
            bars.push(msg.bar);
            vwap.push(msg.vwap);
            vwapPremarket.push(msg.vwap_premarket ?? null);
          }
          return { ...s, bars, vwap, vwapPremarket, error: null };
        });
      } else if (msg.type === "trade") {
        // Shapes the forming candle between two closed bars. The closed bar
        // for the same minute, when it arrives, replaces whatever was built
        // here (the sameMinute branch above), so any drift between this
        // running estimate and Alpaca's own aggregation lasts a minute at
        // most. Prints older than the newest bar are ignored: a late report
        // for a minute that already closed must not reshape a final candle.
        setState((s) => {
          const last = s.bars[s.bars.length - 1];
          if (!last) return s;
          const lastMs = Date.parse(last.t);
          const tradeMs = Date.parse(msg.t);
          if (!Number.isFinite(tradeMs) || tradeMs < lastMs) return s;
          const bars = [...s.bars];
          const vwap = [...s.vwap];
          const vwapPremarket = [...s.vwapPremarket];
          if (tradeMs === lastMs) {
            bars[bars.length - 1] = {
              ...last,
              h: Math.max(last.h, msg.h),
              l: Math.min(last.l, msg.l),
              c: msg.c,
              v: last.v + msg.v,
            };
          } else {
            bars.push({ t: msg.t, o: msg.o, h: msg.h, l: msg.l, c: msg.c, v: msg.v });
            // VWAP is only re-derived server-side per closed bar; carrying
            // the last value forward keeps the arrays aligned with `bars`
            // (aggregateBars indexes them together) without inventing a
            // number.
            vwap.push(vwap[vwap.length - 1] ?? null);
            vwapPremarket.push(vwapPremarket[vwapPremarket.length - 1] ?? null);
          }
          return { ...s, bars, vwap, vwapPremarket };
        });
      } else {
        setState((s) => ({ ...s, error: msg.message }));
      }
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [symbol, settingsNonce]);

  return state;
}
