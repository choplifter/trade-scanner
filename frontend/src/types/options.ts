/** Shapes of /api/trading/options/* (and the /live/ twin). Numbers here are
 * our own floats, not Alpaca's decimal strings -- the backend has already
 * parsed them -- except inside the raw `order` dumps, which stay Alpaca's. */

import type { Order, TradingAccount } from "./trading";

export type Strategy =
  | "long_call"
  | "long_put"
  | "bull_call"
  | "bear_put"
  | "bull_put"
  | "bear_call"
  | "iron_condor"
  | "long_straddle"
  | "long_strangle"
  | "call_butterfly"
  | "put_butterfly"
  | "iron_butterfly"
  | "calendar"
  | "diagonal"
  | "covered_call"
  | "cash_secured_put";
export type OptionKind = "call" | "put";
export type SpreadDirection = "debit" | "credit";

export const STRATEGY_LABELS: Record<Strategy, string> = {
  long_call: "Long call",
  long_put: "Long put",
  bull_call: "Bull call",
  bear_put: "Bear put",
  bull_put: "Bull put",
  bear_call: "Bear call",
  iron_condor: "Iron condor",
  long_straddle: "Straddle",
  long_strangle: "Strangle",
  call_butterfly: "Call fly",
  put_butterfly: "Put fly",
  iron_butterfly: "Iron fly",
  calendar: "Calendar",
  diagonal: "Diagonal",
  covered_call: "Covered call",
  cash_secured_put: "Cash-sec. put",
};

/** The ticket's strategy buttons, grouped. */
export const STRATEGY_GROUPS: { label: string; strategies: Strategy[] }[] = [
  { label: "Long", strategies: ["long_call", "long_put", "long_straddle", "long_strangle"] },
  { label: "Vertical", strategies: ["bull_call", "bear_put", "bull_put", "bear_call"] },
  { label: "Neutral", strategies: ["iron_condor", "iron_butterfly", "call_butterfly", "put_butterfly"] },
  { label: "Time", strategies: ["calendar", "diagonal"] },
  { label: "Income", strategies: ["covered_call", "cash_secured_put"] },
];

/** Strategies described to the backend as an explicit legs list. */
export const LEGS_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>([
  "long_straddle",
  "long_strangle",
  "call_butterfly",
  "put_butterfly",
  "iron_butterfly",
  "calendar",
  "diagonal",
  "covered_call",
  "cash_secured_put",
]);
export const TIME_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>(["calendar", "diagonal"]);
export const INCOME_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>(["covered_call", "cash_secured_put"]);
export const BUTTERFLY_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>(["call_butterfly", "put_butterfly", "iron_butterfly"]);

export const SINGLE_LEG_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>([
  "long_call",
  "long_put",
  "covered_call",
  "cash_secured_put",
]);
export const DEBIT_STRATEGIES: ReadonlySet<Strategy> = new Set<Strategy>([
  "long_call",
  "long_put",
  "bull_call",
  "bear_put",
  "long_straddle",
  "long_strangle",
  "call_butterfly",
  "put_butterfly",
  "calendar",
  "diagonal",
]);
/** Alpaca's options level: 1 writes a covered call / cash-secured put, 2
 * buys a call or put outright, 3 for every spread. */
export function optionsLevelRequired(strategy: Strategy): number {
  if (INCOME_STRATEGIES.has(strategy)) return 1;
  return SINGLE_LEG_STRATEGIES.has(strategy) ? 2 : 3;
}

export interface TicketLeg {
  kind: OptionKind;
  strike: number;
  /** YYYY-MM-DD; omitted = the ticket's expiry. */
  expiry?: string;
  side: "buy" | "sell";
  ratio?: number;
}

/** The risk chart's numbers, per position (x 100 x qty). */
export interface Payoff {
  prices: number[];
  at_expiry: number[];
  today: number[] | null;
  breakevens: number[];
  max_profit: number | null;
  max_loss: number | null;
  spot: number;
  expiry: string;
  multiplier: number;
}

export interface Coverage {
  kind: "shares" | "cash";
  have: number;
  need: number;
  ok: boolean;
}

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
  /** The legs of the newer strategies (see LEGS_STRATEGIES). */
  legs?: TicketLeg[];
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
  gamma?: number | null;
  theta?: number | null;
  iv?: number | null;
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
  /** null = unlimited (a long call). */
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  collateral: number;
  options_buying_power: number | null;
  dte: number;
  options_level: number | null;
  account: TradingAccount;
  warnings: string[];
  client_order_id: string | null;
  coverage: Coverage | null;
  payoff: Payoff | null;
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
  /** The later expiry of a calendar/diagonal. */
  long_expiry: string | null;
  /** Shares backing a covered call. */
  shares: number;
}

export interface PayoffRequest {
  legs: CloseLeg[];
  qty: number;
  /** Per share, signed: positive was paid. */
  net_entry: number;
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
  /** Bounds on the position's own mark (the mid of closing it), per share. */
  premium_below: number | null;
  premium_above: number | null;
  status: TriggerStatus;
  attempts: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  fired_at: string | null;
  fired_price: number | null;
  fired_order_id: string | null;
  /** What fired_price refers to: "underlying" or "premium". */
  fired_on: "underlying" | "premium" | null;
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
  premium_below?: number;
  premium_above?: number;
}

/** "below 740 · above 775 · prem ≤ 1.20" for a trigger row. */
export function triggerBoundsLabel(t: {
  close_below: number | null;
  close_above: number | null;
  premium_below?: number | null;
  premium_above?: number | null;
}): string {
  const parts: string[] = [];
  if (t.close_below != null) parts.push(`below ${t.close_below}`);
  if (t.close_above != null) parts.push(`above ${t.close_above}`);
  if (t.premium_below != null) parts.push(`premium ≤ ${t.premium_below.toFixed(2)}`);
  if (t.premium_above != null) parts.push(`premium ≥ ${t.premium_above.toFixed(2)}`);
  return parts.join(" · ");
}

export interface OrderResponse {
  order: Order;
}
