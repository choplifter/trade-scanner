import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelOrder,
  closePosition,
  getAccount,
  getOrders,
  getPositions,
  replaceStop,
  type CloseResult,
} from "../api/http";
import type { Account, Order, Position } from "../types/trading";

/** Positions and orders change when *you* act, not on every tick, so this
 * polls rather than holding a socket open. Same setInterval + pollRef
 * convention as ScannerBenchmarkWidget; faster because a fill you placed
 * yourself should appear promptly.
 *
 * A push feed (Alpaca's TradingStream) is the eventual upgrade, but it is a
 * second websocket with its own threading caveats -- see the note in
 * StreamManager.subscribe about subscribe calls deadlocking the loop -- and
 * it is not worth that until the panel has earned its place. */
const POLL_MS = 4_000;
/** After you act, "did it fill?" is the only latency that matters, so poll
 * harder for a short window rather than holding a socket open all day. */
const HOT_POLL_MS = 1_200;
const HOT_WINDOW_MS = 12_000;
/** A single failed poll is usually a one-off network blip (or, in dev, the
 * backend mid-restart from --reload) rather than a real outage -- and the
 * very next poll, a second or two later, typically succeeds. Surfacing it
 * immediately flickered an error banner over live positions every time that
 * happened. Wait for a few in a row before treating it as real; a genuine
 * outage still surfaces within a handful of seconds. */
const ERROR_THRESHOLD = 3;

export interface TradingState {
  account: Account | null;
  /** Whether the backend is talking to the simulated account. */
  paper: boolean;
  /** Whether write paths are switched on server-side. */
  tradingEnabled: boolean;
  defaultRiskPct: number;
  positions: Position[];
  orders: Order[];
  loading: boolean;
  error: string | null;
}

const EMPTY_STATE: TradingState = {
  account: null,
  paper: true,
  tradingEnabled: false,
  defaultRiskPct: 1,
  positions: [],
  orders: [],
  loading: true,
  error: null,
};

export interface TradingActions {
  refresh: () => void;
  /** Refetch now and poll harder for a short window -- call after any
   * mutation so the tables do not lag the fill. */
  afterAction: () => void;
  cancel: (orderId: string) => Promise<void>;
  /** With qty, sells part of the position and re-arms its exits for the
   * remainder; the result is returned so the caller can surface stop_lost. */
  close: (symbol: string, qty?: number) => Promise<CloseResult>;
  moveStop: (orderId: string, symbol: string, stopPrice: number) => Promise<void>;
}

export function useTrading(): TradingState & TradingActions {
  const [state, setState] = useState<TradingState>(EMPTY_STATE);
  const cancelledRef = useRef(false);
  const hotUntilRef = useRef(0);
  const failureCountRef = useRef(0);

  const refresh = useCallback(async () => {
    try {
      // One round trip each, in parallel -- they are independent, and a
      // slow order history should not delay the account line.
      const [account, positions, orders] = await Promise.all([
        getAccount(),
        getPositions(),
        getOrders("open"),
      ]);
      if (cancelledRef.current) return;
      failureCountRef.current = 0;
      setState({
        account: account.account,
        paper: account.paper,
        tradingEnabled: account.trading_enabled,
        defaultRiskPct: account.default_risk_pct ?? 1,
        positions: positions.positions,
        orders: orders.orders,
        loading: false,
        error: null,
      });
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      failureCountRef.current += 1;
      if (failureCountRef.current < ERROR_THRESHOLD) return;
      // Keep whatever was last known rather than blanking the panel: a
      // transient failure should not make it look like the positions closed.
      setState((s) => ({
        ...s,
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      }));
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void refresh();
    // One short interval that decides each tick whether it is in the hot
    // window, rather than swapping intervals -- fewer moving parts, and no
    // window where both are running.
    let sinceLast = 0;
    const timer = setInterval(() => {
      sinceLast += HOT_POLL_MS;
      const hot = Date.now() < hotUntilRef.current;
      if (hot || sinceLast >= POLL_MS) {
        sinceLast = 0;
        void refresh();
      }
    }, HOT_POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [refresh]);

  const afterAction = useCallback(() => {
    hotUntilRef.current = Date.now() + HOT_WINDOW_MS;
    void refresh();
  }, [refresh]);

  return {
    ...state,
    refresh: () => void refresh(),
    afterAction,
    cancel: async (orderId: string) => {
      await cancelOrder(orderId);
      afterAction();
    },
    close: async (symbol: string, qty?: number) => {
      const result = await closePosition(symbol, qty);
      afterAction();
      return result;
    },
    moveStop: async (orderId: string, symbol: string, stopPrice: number) => {
      await replaceStop(orderId, symbol, stopPrice);
      afterAction();
    },
  };
}
