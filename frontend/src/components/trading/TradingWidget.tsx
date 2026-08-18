import { useState } from "react";

import { useTrading } from "../../hooks/useTrading";
import type { Account, Order, Position } from "../../types/trading";
import { num } from "../../types/trading";
import { formatPrice } from "../../utils/format";
import { Modal } from "../common/Modal";
import { OrderTicket } from "./OrderTicket";

type Tab = "ticket" | "positions" | "orders" | "account";

const TABS: { id: Tab; label: string }[] = [
  { id: "ticket", label: "Ticket" },
  { id: "positions", label: "Positions" },
  { id: "orders", label: "Orders" },
  { id: "account", label: "Account" },
];

function money(value: string | null | undefined): string {
  const parsed = num(value);
  return parsed === null ? "—" : formatPrice(parsed);
}

function signedPct(value: string | null | undefined): { text: string; cls: string } {
  const parsed = num(value);
  if (parsed === null) return { text: "—", cls: "" };
  // Alpaca reports plpc as a fraction (0.0123), not a percentage.
  const pct = parsed * 100;
  return {
    text: `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`,
    cls: pct === 0 ? "" : pct > 0 ? "delta-up" : "delta-down",
  };
}

function signedMoney(value: string | null | undefined): { text: string; cls: string } {
  const parsed = num(value);
  if (parsed === null) return { text: "—", cls: "" };
  return {
    text: `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)}`,
    cls: parsed === 0 ? "" : parsed > 0 ? "delta-up" : "delta-down",
  };
}

interface TradingWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/** Account state for the connected Alpaca account: open positions, working
 * orders and the balance line. Read-only for now -- order entry lands in the
 * next milestone, behind TRADING_ENABLED and a paper-account check. */
export function TradingWidget({ selectedSymbol, onSelectSymbol }: TradingWidgetProps) {
  const {
    account,
    paper,
    tradingEnabled,
    defaultRiskPct,
    positions,
    orders,
    loading,
    error,
    afterAction,
    cancel,
    close,
  } = useTrading();
  const [tab, setTab] = useState<Tab>("ticket");
  // One pending destructive action at a time, confirmed before it runs.
  // Cancelling a protective stop and flattening a position are both easy to
  // hit by accident in a dense table, and neither is undoable.
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const runPending = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === "cancel") await cancel(pending.id);
      else await close(pending.symbol);
      setPending(null);
      setActionError(null);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Cancelling a stop while still holding the shares leaves the position with
  // no protective exit. Worth saying out loud rather than leaving it to be
  // noticed later.
  const orphansPosition =
    pending?.kind === "cancel" &&
    positions.some((pos) => pos.symbol === pending.symbol && Number(pos.qty) !== 0);

  const count = tab === "positions" ? positions.length : tab === "orders" ? orders.length : 0;

  return (
    <div className="widget trading-widget">
      <div className="widget-header">
        <h2>Trading</h2>
        {/* The single most important thing on this panel is whether the money
            is real. Shown always, not only when it is live. */}
        <span className={paper ? "trading-mode-badge paper" : "trading-mode-badge live"}>
          {paper ? "PAPER" : "LIVE"}
        </span>
        <div className="timeframe-selector">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className="timeframe-button"
              aria-pressed={tab === t.id}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab !== "account" && <span className="widget-count">{count}</span>}
      </div>

      <div className="widget-body">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : loading ? (
          <div className="widget-empty">Loading account…</div>
        ) : tab === "ticket" ? (
          <OrderTicket
            symbol={selectedSymbol}
            defaultRiskPct={defaultRiskPct}
            onSubmitted={afterAction}
          />
        ) : tab === "positions" ? (
          <PositionsTable
            positions={positions}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
            onClosePosition={(symbol) => setPending({ kind: "close", symbol })}
          />
        ) : tab === "orders" ? (
          <OrdersTable
            orders={orders}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
            onCancelOrder={(id, symbol) => setPending({ kind: "cancel", id, symbol })}
          />
        ) : (
          <AccountPanel account={account} paper={paper} tradingEnabled={tradingEnabled} />
        )}
      </div>

      <Modal
        open={pending !== null}
        title={pending?.kind === "cancel" ? "Cancel order" : "Close position"}
        onClose={() => {
          setPending(null);
          setActionError(null);
        }}
      >
        <div className="order-confirm">
          {pending?.kind === "cancel" ? (
            <>
              <p className="order-confirm-line">
                Cancel the working order on <strong>{pending.symbol}</strong>?
              </p>
              {orphansPosition && (
                <p className="order-rejection">
                  You still hold {pending.symbol}. Cancelling this order leaves that position
                  without a protective stop.
                </p>
              )}
            </>
          ) : (
            <p className="order-confirm-line">
              Close the entire <strong>{pending?.symbol}</strong> position at market?
            </p>
          )}
          <p className="order-confirm-mode">PAPER &mdash; simulated account</p>
          {actionError && <p className="order-rejection">{actionError}</p>}
          <div className="order-confirm-actions">
            <button
              type="button"
              className="timeframe-button"
              onClick={() => {
                setPending(null);
                setActionError(null);
              }}
            >
              Keep it
            </button>
            <button
              type="button"
              className="generate-button"
              disabled={busy}
              onClick={() => void runPending()}
            >
              {busy ? "Working" : pending?.kind === "cancel" ? "Cancel order" : "Close position"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

type PendingAction =
  | { kind: "cancel"; id: string; symbol: string }
  | { kind: "close"; symbol: string };

function PositionsTable({
  positions,
  selectedSymbol,
  onSelectSymbol,
  onClosePosition,
}: {
  positions: Position[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onClosePosition: (symbol: string) => void;
}) {
  if (positions.length === 0) {
    return <div className="widget-empty">No open positions.</div>;
  }
  return (
    <table className="performance-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry</th>
          <th>Now</th>
          <th>Value</th>
          <th>P&amp;L</th>
          <th>P&amp;L %</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => {
          const pl = signedMoney(p.unrealized_pl);
          const plpc = signedPct(p.unrealized_plpc);
          return (
            <tr
              key={p.symbol}
              aria-selected={p.symbol === selectedSymbol}
              onClick={() => onSelectSymbol(p.symbol)}
            >
              <td className="symbol-cell">{p.symbol}</td>
              <td>{p.side}</td>
              <td>{num(p.qty) ?? "—"}</td>
              <td>{money(p.avg_entry_price)}</td>
              <td>{money(p.current_price)}</td>
              <td>{money(p.market_value)}</td>
              <td className={pl.cls}>{pl.text}</td>
              <td className={plpc.cls}>{plpc.text}</td>
              <td>
                <button
                  type="button"
                  className="row-action"
                  onClick={(e) => {
                    e.stopPropagation();
                    onClosePosition(p.symbol);
                  }}
                >
                  Close
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function OrdersTable({
  orders,
  selectedSymbol,
  onSelectSymbol,
  onCancelOrder,
}: {
  orders: Order[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onCancelOrder: (id: string, symbol: string) => void;
}) {
  if (orders.length === 0) {
    return <div className="widget-empty">No working orders.</div>;
  }
  return (
    <table className="performance-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Side</th>
          <th>Type</th>
          <th>Qty</th>
          <th>Filled</th>
          <th>Limit</th>
          <th>Stop</th>
          <th>Status</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <tr
            key={o.id}
            aria-selected={o.symbol === selectedSymbol}
            onClick={() => onSelectSymbol(o.symbol)}
          >
            <td className="symbol-cell">{o.symbol}</td>
            <td>{o.side}</td>
            <td>{o.order_type}</td>
            <td>{num(o.qty) ?? "—"}</td>
            <td>{num(o.filled_qty) ?? "—"}</td>
            <td>{money(o.limit_price)}</td>
            <td>{money(o.stop_price)}</td>
            <td>{o.status}</td>
            <td>
              <button
                type="button"
                className="row-action"
                onClick={(e) => {
                  // The row click selects the symbol; without this, cancelling
                  // would also retarget the chart.
                  e.stopPropagation();
                  onCancelOrder(o.id, o.symbol);
                }}
              >
                Cancel
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AccountPanel({
  account,
  paper,
  tradingEnabled,
}: {
  account: Account | null;
  paper: boolean;
  tradingEnabled: boolean;
}) {
  if (!account) return <div className="widget-empty">No account data.</div>;

  const equity = num(account.equity);
  const lastEquity = num(account.last_equity);
  const dayPl = equity !== null && lastEquity !== null ? equity - lastEquity : null;
  const dayPlPct = dayPl !== null && lastEquity ? (dayPl / lastEquity) * 100 : null;
  const dayPlText =
    dayPl === null
      ? "—"
      : `${dayPl > 0 ? "+" : ""}${dayPl.toFixed(2)}${
          dayPlPct === null ? "" : ` (${dayPlPct > 0 ? "+" : ""}${dayPlPct.toFixed(2)}%)`
        }`;

  const rows: { label: string; value: string; cls?: string }[] = [
    { label: "Equity", value: money(account.equity) },
    {
      label: "Day P&L",
      value: dayPlText,
      cls: dayPl === null || dayPl === 0 ? "" : dayPl > 0 ? "delta-up" : "delta-down",
    },
    { label: "Cash", value: money(account.cash) },
    { label: "Buying power", value: money(account.buying_power) },
    { label: "Long value", value: money(account.long_market_value) },
    { label: "Short value", value: money(account.short_market_value) },
    { label: "Status", value: account.status },
    {
      label: "Day trades",
      value: account.daytrade_count === null ? "—" : String(account.daytrade_count),
    },
  ];

  return (
    <div className="trading-account">
      <table className="performance-table">
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td className="trading-account-label">{row.label}</td>
              <td className={row.cls ?? ""}>{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="trading-account-note">
        {paper
          ? "Simulated account — no real money is at risk."
          : "LIVE account. Order placement is refused by this build."}{" "}
        Order entry is {tradingEnabled ? "enabled" : "disabled"} (TRADING_ENABLED).
      </p>
    </div>
  );
}
