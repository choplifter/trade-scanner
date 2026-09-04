import { useCallback, useEffect, useState } from "react";

import { connectBroker, disconnectBroker, getBrokerStatus } from "../../api/broker";
import { OrderRejectedError } from "../../api/http";
import { brokerChanged } from "../../api/settingsDialog";
import { LIVE_CONFIRMATION } from "../../api/tradingMode";
import type { BrokerAccountStatus, BrokerStatusResponse } from "../../types/broker";
import type { TradingAccount } from "../../types/trading";
import { formatMoney } from "../../utils/format";
import { LiveConfirmField } from "../trading/LiveConfirmField";

function errorText(err: unknown): string {
  if (err instanceof OrderRejectedError) return err.detail.message;
  return err instanceof Error ? err.message : String(err);
}

function AccountCard({
  account,
  status,
  isAdmin,
  allowLive,
  onChanged,
}: {
  account: TradingAccount;
  status: BrokerAccountStatus | null;
  isAdmin: boolean;
  allowLive: boolean;
  onChanged: () => void;
}) {
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const live = account === "live";
  const connected = status?.connected === true;
  const showForm = !connected || editing;
  const canSubmit = keyId.trim().length >= 8 && secret.trim().length >= 8 && (!live || typed.trim() === LIVE_CONFIRMATION);

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      await connectBroker(account, keyId.trim(), secret.trim(), live ? typed.trim() : undefined);
      setKeyId("");
      setSecret("");
      setTyped("");
      setEditing(false);
      brokerChanged();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      await disconnectBroker(account);
      brokerChanged();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`broker-card${live ? " live-frame" : ""}`}>
      <div className="broker-card-head">
        <span className={`trading-mode-badge ${live ? "live" : "paper"}`}>{live ? "LIVE" : "PAPER"}</span>
        <span className="broker-card-title">{live ? "Real-money account" : "Paper account"}</span>
        <span className={`broker-state${connected ? " connected" : ""}`}>
          {connected ? "● connected" : status?.error ? "● keys refused" : "○ not connected"}
        </span>
      </div>

      {status && (connected || status.error) && (
        <dl className="broker-facts">
          <dt>Keys</dt>
          <dd>
            …{status.key_hint ?? "????"}{" "}
            <span className="order-hint">
              {status.source === "env" ? "(from backend/.env -- the operator's keys)" : "(entered here)"}
            </span>
          </dd>
          {status.account_number && (
            <>
              <dt>Account</dt>
              <dd>
                {status.account_number}
                {status.status ? ` · ${status.status}` : ""}
                {status.options_trading_level != null ? ` · options level ${status.options_trading_level}` : ""}
              </dd>
            </>
          )}
          {status.equity != null && (
            <>
              <dt>Equity</dt>
              <dd>
                {formatMoney(status.equity)}
                {status.buying_power != null ? ` · buying power ${formatMoney(status.buying_power)}` : ""}
              </dd>
            </>
          )}
          {status.error && (
            <>
              <dt>Problem</dt>
              <dd className="order-rejection">{status.error}</dd>
            </>
          )}
        </dl>
      )}

      {live && !allowLive && (
        <p className="order-hint">
          Real-money orders stay switched off on this server (TRADING_ALLOW_LIVE). You can still connect the
          account to see it.
        </p>
      )}

      {showForm ? (
        <div className="broker-form">
          <label>
            API key ID
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              value={keyId}
              placeholder={live ? "AK…" : "PK…"}
              onChange={(e) => setKeyId(e.target.value)}
            />
          </label>
          <label>
            API secret key
            <input
              type="password"
              autoComplete="new-password"
              value={secret}
              placeholder="never shown again"
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>
          {live && <LiveConfirmField mode="live" value={typed} onChange={setTyped} />}
          <div className="broker-actions">
            <button
              type="button"
              className={`generate-button${live ? " live-action" : ""}`}
              disabled={busy || !canSubmit}
              onClick={() => void connect()}
            >
              {busy ? "Checking…" : "Connect & verify"}
            </button>
            {connected && (
              <button type="button" className="row-action" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
            )}
          </div>
          <p className="order-hint">
            The pair is checked against Alpaca once and stored encrypted. Create keys at{" "}
            {live ? "app.alpaca.markets (live)" : "app.alpaca.markets → Paper trading"}.
          </p>
        </div>
      ) : (
        <div className="broker-actions">
          {status?.source === "user" ? (
            <>
              <button type="button" className="row-action" disabled={busy} onClick={() => setEditing(true)}>
                Replace keys
              </button>
              <button type="button" className="row-action" disabled={busy} onClick={() => void disconnect()}>
                Disconnect
              </button>
            </>
          ) : (
            <>
              <span className="order-hint">
                {isAdmin ? "Using the operator's keys from backend/.env." : ""}
              </span>
              <button type="button" className="row-action" disabled={busy} onClick={() => setEditing(true)}>
                Use my own keys instead
              </button>
            </>
          )}
        </div>
      )}
      {error && <p className="order-rejection">{error}</p>}
    </div>
  );
}

/** Settings → Broker: the user's own Alpaca accounts. Market data keeps
 * running on the operator's subscription; orders, positions, history,
 * options and triggers use whichever account is connected here. */
export function BrokerTab() {
  const [status, setStatus] = useState<BrokerStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getBrokerStatus()
      .then((res) => {
        setStatus(res);
        setError(null);
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="settings-section broker-tab">
      <p className="order-hint">
        Every login trades on its own Alpaca account: connect your paper key pair (and, if you want, your
        real-money pair) here. Market data -- charts, chains, news -- comes from the operator's subscription
        regardless. Simulation mode needs no broker.
      </p>
      {error && <p className="order-rejection">{error}</p>}
      {!status && !error && <p className="order-hint">Checking…</p>}
      {status && (
        <>
          <div className="broker-card broker-data">
            <div className="broker-card-head">
              <span className="broker-card-title">Market data</span>
              <span className={`broker-state${status.market_data.key_hint ? " connected" : ""}`}>
                {status.market_data.key_hint
                  ? `● keys …${status.market_data.key_hint} (${status.market_data.source === "admin" ? "admin's stored paper pair" : "backend/.env"})`
                  : "○ no keys"}
              </span>
            </div>
            <p className="order-hint">
              Charts, chains, news and the scanner run on the operator's key pair: the first admin's paper keys
              from this dialog, or backend/.env when none are stored. The data subscription is tied to that
              account.
            </p>
            {status.market_data.restart_required && (
              <p className="order-rejection">
                Restart the backend to switch market data to the stored admin keys (…
                {status.market_data.next_key_hint}). Trading already uses them.
              </p>
            )}
          </div>
          <AccountCard
            account="paper"
            status={status.accounts.paper ?? null}
            isAdmin={status.is_admin}
            allowLive={status.trading_allow_live}
            onChanged={load}
          />
          <AccountCard
            account="live"
            status={status.accounts.live ?? null}
            isAdmin={status.is_admin}
            allowLive={status.trading_allow_live}
            onChanged={load}
          />
          {!status.trading_enabled && (
            <p className="order-hint">
              Order placement is switched off on this server (TRADING_ENABLED); connected accounts are read-only
              until the operator turns it on.
            </p>
          )}
        </>
      )}
    </div>
  );
}
