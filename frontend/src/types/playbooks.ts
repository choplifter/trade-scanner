/**
 * Playbooks -- backend app/playbooks: scripts that run options campaigns
 * (the Wheel first), the campaigns themselves, their events and proposals.
 */

import type { CloseSpreadRequest, RollRequest, SpreadTicketRequest } from "./options";

export type ParamType = "int" | "float" | "bool";

export interface ParamSpec {
  name: string;
  type: ParamType;
  default: number | boolean;
  label: string;
  min: number | null;
  max: number | null;
  step: number | null;
  help: string;
}

export interface PlaybookScript {
  name: string;
  stem: string;
  filename: string;
  description: string;
  params: ParamSpec[];
}

export interface PlaybookScriptsResponse {
  playbooks: PlaybookScript[];
  errors: { filename: string; error: string }[];
}

export type CampaignStatus = "active" | "paused" | "closed";
export type CampaignPhase = "cash" | "short_put" | "assigned" | "covered_call" | "mixed";

export type ProposalKind = "sell_put" | "sell_call" | "roll" | "close" | "hold";

/** What the playbook proposes next: a sentence, its reason, and the
 * executable part -- an income ticket, a roll, or a close. */
export interface Proposal {
  kind: ProposalKind;
  sentence: string;
  reason: string;
  ticket: SpreadTicketRequest | null;
  roll: RollRequest | null;
  close: CloseSpreadRequest | null;
  computed_at: string;
  spot: number;
  phase: CampaignPhase;
  expiries_loaded: string[];
  key: string | null;
}

export type EventKind =
  | "started"
  | "sold_put"
  | "sold_call"
  | "closed"
  | "rolled"
  | "expired"
  | "cash_settled"
  | "assigned"
  | "called_away"
  | "shares_changed"
  | "manual_note"
  | "paused"
  | "resumed"
  | "campaign_closed"
  | "executed"
  | "execute_failed";

export interface CampaignEvent {
  id: string;
  campaign_id: string;
  at: string;
  kind: EventKind;
  occ: string | null;
  qty: number;
  price: number | null;
  /** Dollars, signed: a credit positive. */
  cash_delta: number | null;
  order_id: string | null;
  note: string | null;
}

export interface Campaign {
  id: string;
  user_id: number;
  account: string;
  symbol: string;
  playbook: string;
  params: Record<string, number | boolean>;
  status: CampaignStatus;
  phase: CampaignPhase;
  auto_execute: boolean;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  shares: number;
  shares_avg_entry: number | null;
  /** Per share: (avg entry × shares − premiums collected) / shares. */
  cost_basis: number | null;
  premiums_collected: number;
  realized_pnl: number;
  proposal: Proposal | null;
  proposal_at: string | null;
  proposal_error: string | null;
  orders_cursor: string | null;
  executed_order_id: string | null;
  last_error: string | null;
  events: CampaignEvent[];
}

export interface CampaignCreate {
  account: "sim";
  symbol: string;
  playbook: string;
  params: Record<string, number | boolean>;
  auto_execute?: boolean;
}

export interface CampaignPatch {
  status?: CampaignStatus;
  params?: Record<string, number | boolean>;
  auto_execute?: boolean;
}

/** POST /playbooks/backtest -- backend app/playbooks/backtest.py. */
export interface BacktestRequest {
  symbol: string;
  playbook: string;
  params: Record<string, number | boolean>;
  months: number;
  iv_premium: number;
  starting_cash: number;
  spread_frac: number;
}

export interface BacktestPoint {
  date: string;
  equity: number;
  benchmark: number;
  spot: number;
  shares: number;
  legs: number;
}

export interface BacktestEvent {
  at: string;
  kind: EventKind;
  occ: string | null;
  qty: number;
  price: number | null;
  cash_delta: number | null;
  note: string | null;
}

export interface BacktestSummary {
  starting_cash: number;
  final_equity: number;
  total_return_pct: number;
  buy_and_hold_return_pct: number;
  premiums: number;
  realized_pnl: number;
  puts_sold: number;
  calls_sold: number;
  rolls: number;
  assignments: number;
  called_away: number;
  expired: number;
  max_drawdown_pct: number;
  days_in_shares_pct: number;
  shares_at_end: number;
  cost_basis_at_end: number | null;
}

export interface BacktestResult {
  symbol: string;
  playbook: string;
  params: Record<string, number | boolean>;
  synthetic: true;
  iv_premium: number;
  spread_frac: number;
  sessions: number;
  from: string;
  to: string;
  equity: BacktestPoint[];
  events: BacktestEvent[];
  summary: BacktestSummary;
  disclaimer: string;
}
