/** Shapes of /api/broker/* -- the user's own Alpaca key pairs (backend
 * app.broker). Secrets never come back; `key_hint` is the key id's last
 * characters. */

import type { TradingAccount } from "./trading";

export interface BrokerAccountStatus {
  account: TradingAccount;
  connected: boolean;
  /** "user": keys entered in Settings; "env": the operator's keys from
   * backend/.env (admin only); null when nothing is connected. */
  source: "user" | "env" | null;
  key_hint: string | null;
  account_number?: string | null;
  status?: string | null;
  options_trading_level?: number | null;
  buying_power?: number | null;
  equity?: number | null;
  /** Set when stored keys exist but Alpaca refused them. */
  error?: string | null;
}

export interface BrokerStatusResponse {
  accounts: Record<TradingAccount, BrokerAccountStatus>;
  is_admin: boolean;
  trading_enabled: boolean;
  trading_allow_live: boolean;
  /** Which key pair the running backend built its market-data clients
   * from: the first admin's stored paper pair ("admin") or backend/.env
   * ("env"). `restart_required` when the stored admin pair differs from
   * the running one -- market data switches at the next restart. */
  market_data: {
    source: "admin" | "env";
    key_hint: string | null;
    restart_required: boolean;
    next_source: "admin" | "env";
    next_key_hint: string | null;
  };
}
