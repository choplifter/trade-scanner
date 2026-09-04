import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelOrder,
  closePosition,
  getAccount,
  getOrders,
  getPositions,
  replaceLimit,
  replaceStop,
  replaceTarget,
  type CloseResult,
} from "../api/http";
import type { Account, AccountLimits, Order, Position, TradingAccount } from "../types/trading";

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
import { OrderRejectedError } from "../api/http";
import { subscribeBrokerChanged } from "../api/settingsDialog";

const ERROR_THRESHOLD = 3;

/** Draft entry/stop/target from the order ticket currently being built --
 * not a real position, just what's typed/suggested right now. Published by
 * OrderTicket, read by ChartWidget so the two siblings can share it without
 * prop-drilling through App.tsx (same reason positions/orders live here).
 * Carries its own `symbol` so a reader can refuse to draw a stale ticket's
 * levels over a chart that's since moved to a different one. */
export interface IndicativeLevels {
  symbol: string;
  side: "long" | "short";
  entry: number | null;
  stop: number | null;
  target: number | null;
  /** Called when the chart's draft Stop/Target line is dragged to a new
   * price -- writes straight back into the ticket's own Stop/Target input,
   * the same as if the user had retyped it. There's no order to move here
   * (nothing has been submitted yet), so unlike a real position's drag this
   * can't fail or round-trip a network call. Optional because a reader with
   * no way to edit the ticket that produced these levels (there isn't one
   * today, but nothing guarantees there won't be) has nothing to call. */
  onDragStop?: (price: number) => void;
  onDragTarget?: (price: number) => void;
}

export interface TradingState {
  account: Account | null;
  /** Which Alpaca account the current poll answered for. */
  tradingAccount: TradingAccount;
  /** Whether the backend is talking to the simulated account. */
  paper: boolean;
  liveAvailable: boolean;
  liveAllowed: boolean;
  limits: AccountLimits | null;
  /** Whether write paths are switched on server-side. */
  tradingEnabled: boolean;
  defaultRiskPct: number;
  positions: Position[];
  orders: Order[];
  loading: boolean;
  error: string | null;
  /** This user has no Alpaca key pair for the account (backend
   * "broker_not_connected"): the panel offers Settings → Broker instead
   * of an error. */
  brokerMissing: boolean;
  /** Which keys answer: the user's own, or the operator's from .env. */
  brokerInfo: { source?: string; key_hint?: string | null; account_number?: string | null } | null;
}

const EMPTY_STATE: TradingState = {
  account: null,
  tradingAccount: "paper",
  paper: true,
  liveAvailable: false,
  liveAllowed: false,
  limits: null,
  tradingEnabled: false,
  defaultRiskPct: 1,
  positions: [],
  orders: [],
  loading: true,
  error: null,
  brokerMissing: false,
  brokerInfo: null,
};

export interface TradingActions {
  refresh: () => void;
  /** Refetch now and poll harder for a short window -- call after any
   * mutation so the tables do not lag the fill. */
  afterAction: () => void;
  /** `confirm` is the typed LIVE from a live-mode dialog; ignored on paper. */
  cancel: (orderId: string, confirm?: string) => Promise<void>;
  /** With qty, sells part of the position and re-arms its exits for the
   * remainder; the result is returned so the caller can surface stop_lost. */
  close: (symbol: string, qty?: number, confirm?: string) => Promise<CloseResult>;
  moveStop: (orderId: string, symbol: string, stopPrice: number, confirm?: string) => Promise<void>;
  /** Same as moveStop, for the take-profit leg (see OrderService.replace_target). */
  moveTarget: (orderId: string, symbol: string, limitPrice: number, confirm?: string) => Promise<void>;
  /** Re-price a working plain limit entry (see OrderService.replace_limit). */
  moveLimit: (orderId: string, symbol: string, limitPrice: number, confirm?: string) => Promise<void>;
  /** See IndicativeLevels. Kept out of TradingState/EMPTY_STATE on purpose:
   * that object is fully replaced by every poll tick's setState in
   * refresh(), which would silently wipe this out unless every such call
   * remembered to carry it forward. It's genuinely more a value+setter pair
   * than an "action", but living next to setIndicativeLevels here avoids
   * that footgun entirely. */
  indicativeLevels: IndicativeLevels | null;
  setIndicativeLevels: (levels: IndicativeLevels | null) => void;
}

export function useTrading(): TradingState & TradingActions {
  const [state, setState] = useState<TradingState>(EMPTY_STATE);
  const [indicativeLevels, setIndicativeLevels] = useState<IndicativeLevels | null>(null);
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
        tradingAccount: account.trading_account ?? "paper",
        paper: account.paper,
        liveAvailable: account.live_available ?? false,
        liveAllowed: account.live_allowed ?? false,
        limits: account.limits ?? null,
        tradingEnabled: account.trading_enabled,
        defaultRiskPct: account.default_risk_pct ?? 1,
        positions: positions.positions,
        orders: orders.orders,
        loading: false,
        error: null,
        brokerMissing: false,
        brokerInfo: account.broker ?? null,
      });
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      // No key pair for this account: not a transient failure, so no
      // threshold -- the panel switches to "connect your broker" at once.
      if (err instanceof OrderRejectedError && err.detail.code === "broker_not_connected") {
        failureCountRef.current = 0;
        setState((s) => ({
          ...EMPTY_STATE,
          tradingAccount: s.tradingAccount,
          loading: false,
          brokerMissing: true,
        }));
        return;
      }
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

  // A key pair connected or removed in Settings → Broker: refetch at once.
  useEffect(() => subscribeBrokerChanged(() => void refresh()), [refresh]);

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
    cancel: async (orderId: string, confirm?: string) => {
      await cancelOrder(orderId, confirm);
      afterAction();
    },
    close: async (symbol: string, qty?: number, confirm?: string) => {
      const result = await closePosition(symbol, qty, confirm);
      afterAction();
      return result;
    },
    moveStop: async (orderId: string, symbol: string, stopPrice: number, confirm?: string) => {
      await replaceStop(orderId, symbol, stopPrice, confirm);
      afterAction();
    },
    moveLimit: async (orderId: string, symbol: string, limitPrice: number, confirm?: string) => {
      await replaceLimit(orderId, symbol, limitPrice, confirm);
      afterAction();
    },
    moveTarget: async (orderId: string, symbol: string, limitPrice: number, confirm?: string) => {
      await replaceTarget(orderId, symbol, limitPrice, confirm);
      afterAction();
    },
    indicativeLevels,
    setIndicativeLevels,
  };
}
