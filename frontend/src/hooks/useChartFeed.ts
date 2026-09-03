import { useEffect, useRef, useState } from "react";

import { getSymbolBars } from "../api/http";
import { chartSocket } from "../api/ws";
import type { Bar, ChartQuoteMessage, IndicatorResult } from "../types/alpaca";
import { parseOcc } from "../utils/occ";
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
  /** The newest bid/ask, option contracts only (from the option stream). */
  quote: ChartQuoteMessage | null;
  error: string | null;
  loading: boolean;
}

/** How often a premium chart re-fetches its newest bars. */
const OPTION_POLL_MS = 5000;

const EMPTY_STATE: ChartFeedState = {
  bars: [],
  vwap: [],
  vwapPremarket: [],
  indicators: [],
  quote: null,
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
  // Read by the premium chart's refresh poll without being a dependency.
  const stateRef = useRef(state);
  stateRef.current = state;
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

    // An option contract (premium chart): live trades and quotes arrive
    // over the socket like a stock's (subscribed below), but there is no
    // closed-bar stream for options, so the newest bars are also
    // re-fetched every few seconds (`since` a minute before the last bar
    // held) and merged in by minute -- that is what finalises a candle.
    const poll = parseOcc(symbol)
      ? window.setInterval(() => {
        if (cancelled || document.hidden) return;
        const held = stateRef.current.bars;
        const last = held[held.length - 1];
        const since = last ? Date.parse(last.t) / 1000 - 60 : undefined;
        getSymbolBars(symbol, "1Min", since)
          .then((res) => {
            if (cancelled || res.bars.length === 0) return;
            setState((s) => {
              const bars = [...s.bars];
              for (const bar of res.bars) {
                const barMs = Date.parse(bar.t);
                let index = bars.length - 1;
                while (index >= 0 && Date.parse(bars[index].t) > barMs)
                  index -= 1;
                if (index >= 0 && Date.parse(bars[index].t) === barMs)
                  bars[index] = bar;
                else bars.splice(index + 1, 0, bar);
              }
              return {
                ...s,
                bars,
                vwap: bars.map(() => null),
                vwapPremarket: bars.map(() => null),
                error: null,
              };
            });
          })
          .catch(() => {
            // A failed refresh keeps the bars already shown; the next
            // tick tries again.
          });
      }, OPTION_POLL_MS)
      : null;

    const unsubscribe = chartSocket.subscribe(symbol, (msg) => {
      if (msg.type === "bar") {
        setState((s) => {
          const bars = [...s.bars];
          const vwap = [...s.vwap];
          const vwapPremarket = [...s.vwapPremarket];
          // Alpaca closes minute M a few seconds after M+1 has begun, by
          // which time the trade path below has usually already opened the
          // M+1 candle -- so the closed bar mostly belongs one slot *before*
          // the newest bar, not at the end. Matched by minute (as instants,
          // not strings: the synthesised bar's timestamp is formatted by the
          // trade path) searching back from the end; a bar for a minute no
          // one has seen yet is inserted at its sorted position. Appending
          // blindly put M after M+1, and lightweight-charts rejects
          // out-of-order data outright.
          const barMs = Date.parse(msg.bar.t);
          let index = bars.length - 1;
          while (index >= 0 && Date.parse(bars[index].t) > barMs) index -= 1;
          if (index >= 0 && Date.parse(bars[index].t) === barMs) {
            bars[index] = msg.bar;
            vwap[index] = msg.vwap;
            vwapPremarket[index] = msg.vwap_premarket ?? null;
            // Candles synthesised after this one carried its provisional
            // VWAP forward; hand them the settled value.
            for (let j = index + 1; j < bars.length; j++) {
              vwap[j] = msg.vwap;
              vwapPremarket[j] = msg.vwap_premarket ?? null;
            }
          } else {
            bars.splice(index + 1, 0, msg.bar);
            vwap.splice(index + 1, 0, msg.vwap);
            vwapPremarket.splice(index + 1, 0, msg.vwap_premarket ?? null);
          }
          return { ...s, bars, vwap, vwapPremarket, error: null };
        });
      } else if (msg.type === "trade") {
        // Shapes the forming candle between two closed bars. The closed bar
        // for the same minute, when it arrives, replaces whatever was built
        // here (matched by minute in the bar branch above), so any drift between this
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
            bars.push({
              t: msg.t,
              o: msg.o,
              h: msg.h,
              l: msg.l,
              c: msg.c,
              v: msg.v,
            });
            // VWAP is only re-derived server-side per closed bar; carrying
            // the last value forward keeps the arrays aligned with `bars`
            // (aggregateBars indexes them together) without inventing a
            // number.
            vwap.push(vwap[vwap.length - 1] ?? null);
            vwapPremarket.push(vwapPremarket[vwapPremarket.length - 1] ?? null);
          }
          return { ...s, bars, vwap, vwapPremarket };
        });
      } else if (msg.type === "quote") {
        setState((s) => ({ ...s, quote: msg }));
      } else {
        setState((s) => ({ ...s, error: msg.message }));
      }
    });

    return () => {
      cancelled = true;
      if (poll != null) window.clearInterval(poll);
      unsubscribe();
    };
  }, [symbol, settingsNonce]);

  return state;
}
