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
  market_data_source: "operator";
}
