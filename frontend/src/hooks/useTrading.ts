import { useCallback, useEffect, useRef, useState } from "react";

import { getAccount, getOrders, getPositions } from "../api/http";
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

export function useTrading(): TradingState & { refresh: () => void } {
  const [state, setState] = useState<TradingState>(EMPTY_STATE);
  const cancelledRef = useRef(false);

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
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [refresh]);

  return { ...state, refresh: () => void refresh() };
}
