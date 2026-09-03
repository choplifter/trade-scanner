/** Shapes of /api/trading/options/* (and the /live/ twin). Numbers here are
 * our own floats, not Alpaca's decimal strings -- the backend has already
 * parsed them -- except inside the raw `order` dumps, which stay Alpaca's. */

import type { Order, TradingAccount } from "./trading";

export type Strategy = "bull_call" | "bear_put" | "bull_put" | "bear_call" | "iron_condor";
export type OptionKind = "call" | "put";
export type SpreadDirection = "debit" | "credit";

export const STRATEGY_LABELS: Record<Strategy, string> = {
  bull_call: "Bull call",
  bear_put: "Bear put",
  bull_put: "Bull put",
  bear_call: "Bear call",
  iron_condor: "Iron condor",
};

export const DEBIT_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>(["bull_call", "bear_put"]);

export interface OptionsAccountResponse {
  account: TradingAccount;
  options_buying_power: number | null;
  buying_power: number | null;
  equity: number | null;
  options_approved_level: number | null;
  options_trading_level: number | null;
  can_submit: boolean;
  feed: "opra" | "indicative";
  limits: { account: TradingAccount; max_contracts: number; max_order_notional: number };
}

export interface ExpiryInfo {
  expiry: string; // YYYY-MM-DD
  dte: number;
  contract_count: number;
}

export interface ExpiriesResponse {
  underlying: string;
  spot: number;
  expiries: ExpiryInfo[];
}

export interface LegQuote {
  symbol: string;
  strike: number;
  kind: OptionKind;
  expiry: string;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  last: number | null;
  bid_size: number | null;
  ask_size: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  iv: number | null;
  open_interest: number;
  tradable: boolean;
}

export interface StrikeRow {
  strike: number;
  call: LegQuote | null;
  put: LegQuote | null;
}

export interface ChainResponse {
  underlying: string;
  expiry: string;
  spot: number;
  feed: string;
  as_of: string;
  rows: StrikeRow[];
}

export interface SpreadTicketRequest {
  underlying: string;
  strategy: Strategy;
  expiry: string;
  qty: number;
  long_strike?: number;
  short_strike?: number;
  put_long_strike?: number;
  put_short_strike?: number;
  call_short_strike?: number;
  call_long_strike?: number;
  /** Positive net price per spread; omitted = current mid. */
  limit_price?: number;
  client_order_id?: string;
}

export interface SpreadLeg {
  symbol: string;
  kind: OptionKind;
  strike: number;
  expiry: string;
  side: "buy" | "sell";
  position_intent: "buy_to_open" | "sell_to_open" | "buy_to_close" | "sell_to_close";
  ratio_qty: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  delta: number | null;
}

export interface ResolvedSpread {
  underlying: string;
  strategy: Strategy;
  expiry: string;
  qty: number;
  direction: SpreadDirection;
  legs: SpreadLeg[];
  spot: number;
  width: number;
  net_mid: number;
  net_natural: number | null;
  limit_price: number;
  /** +debit / -credit, what the MLEG order carries. */
  alpaca_limit_price: number;
  max_profit: number;
  max_loss: number;
  breakevens: number[];
  collateral: number;
  options_buying_power: number | null;
  dte: number;
  options_level: number | null;
  account: TradingAccount;
  warnings: string[];
  client_order_id: string | null;
}

export interface SpreadPreview {
  spread: ResolvedSpread;
  can_submit: boolean;
  limits: { account: TradingAccount; max_contracts: number; max_order_notional: number };
}

export interface SpreadPositionLeg {
  symbol: string;
  kind: OptionKind;
  strike: number;
  /** Signed: long positive, short negative. */
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  cost_basis: number;
}

export type SpreadGroupStrategy = Strategy | "custom" | "broken";

export interface SpreadGroup {
  id: string;
  underlying: string;
  root: string;
  expiry: string;
  dte: number;
  strategy: SpreadGroupStrategy;
  qty: number;
  legs: SpreadPositionLeg[];
  /** Per share, signed like a ticket price: positive was paid. */
  net_entry: number;
  market_value: number;
  unrealized_pl: number;
  broken: boolean;
  account: TradingAccount;
}

export type TriggerStatus = "active" | "fired" | "cancelled" | "failed" | "orphaned";

export interface UnderlyingTrigger {
  id: string;
  user_id: number;
  account: TradingAccount;
  underlying: string;
  expiry: string;
  legs: { symbol: string; qty: number }[];
  qty: number;
  close_below: number | null;
  close_above: number | null;
  status: TriggerStatus;
  attempts: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  fired_at: string | null;
  fired_price: number | null;
  fired_order_id: string | null;
}

export interface SpreadsResponse {
  spreads: SpreadGroup[];
  triggers: UnderlyingTrigger[];
}

export interface CloseLeg {
  symbol: string;
  qty: number;
}

export interface CloseSpreadRequest {
  legs: CloseLeg[];
  qty: number;
  limit_price?: number;
  client_order_id?: string;
}

export interface ClosePreview {
  legs: SpreadLeg[];
  qty: number;
  direction: SpreadDirection;
  net_mid: number;
  net_natural: number | null;
  suggested_limit: number;
  alpaca_limit_price: number;
}

export interface TriggerCreateRequest {
  underlying: string;
  expiry: string;
  legs: CloseLeg[];
  qty: number;
  close_below?: number;
  close_above?: number;
}

export interface OrderResponse {
  order: Order;
}
