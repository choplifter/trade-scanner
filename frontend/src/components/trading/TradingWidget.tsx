import { useState } from "react";

import { useBalanceHistory } from "../../hooks/useBalanceHistory";
import { useOrderHistory } from "../../hooks/useOrderHistory";
import { useTrades } from "../../hooks/useTrades";
import type { TradesState } from "../../hooks/useTrades";
import { useTrading } from "../../hooks/useTrading";
import type {
  Account,
  BalanceRange,
  Order,
  Position,
  PortfolioHistoryResponse,
  Trade,
  TradeSummary,
} from "../../types/trading";
import { exitsForPosition, num } from "../../types/trading";
import { formatPrice } from "../../utils/format";
import { Modal } from "../common/Modal";
import { BalanceChart } from "./BalanceChart";
import { OrderTicket } from "./OrderTicket";

type Tab = "ticket" | "positions" | "orders" | "balance" | "account";

/** Working orders and completed fills are both "orders", but one is a thing
 * you can still act on and the other is a record. Trades are the fills
 * paired back into round trips -- the record that answers "which position
 * made or lost what", which neither of the other two can. Same tab, three
 * views. */
type OrdersView = "working" | "filled" | "trades";

const TABS: { id: Tab; label: string }[] = [
  { id: "ticket", label: "Ticket" },
  { id: "positions", label: "Positions" },
  { id: "orders", label: "Orders" },
  { id: "balance", label: "Balance" },
  { id: "account", label: "Account" },
];

const BALANCE_RANGES: BalanceRange[] = ["1D", "1W", "1M", "3M", "1Y", "ALL"];

/** Ranges whose points are one-per-session, so the chart labels them as
 * dates. Mirrors _DAILY_TIMEFRAMES on the backend, matched on the timeframe
 * the response reports rather than on the range, so the two cannot drift. */
const DAILY_TIMEFRAME = "1D";

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

/** Like signedMoney/signedPct, but for the numbers the trades endpoint
 * returns -- already numbers, already in the unit shown. */
function signedNumber(
  value: number | null,
  digits: number,
  suffix = "",
): { text: string; cls: string } {
  if (value === null || !Number.isFinite(value)) return { text: "—", cls: "" };
  return {
    text: `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`,
    cls: value === 0 ? "" : value > 0 ? "delta-up" : "delta-down",
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
    moveStop,
  } = useTrading();
  const [tab, setTab] = useState<Tab>("ticket");
  const [ordersView, setOrdersView] = useState<OrdersView>("working");
  const [balanceRange, setBalanceRange] = useState<BalanceRange>("1M");
  // Both hooks are held here rather than inside their panels so the header
  // count can read them, and both stay idle until their tab is open.
  const orderHistory = useOrderHistory(tab === "orders" && ordersView === "filled");
  const tradeHistory = useTrades(tab === "orders" && ordersView === "trades");
  const balance = useBalanceHistory(balanceRange, tab === "balance");
  // One pending destructive action at a time, confirmed before it runs.
  // Cancelling a protective stop and flattening a position are both easy to
  // hit by accident in a dense table, and neither is undoable.
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A partial close that could not re-arm the remainder's stop must stay
  // visible after the modal is gone -- re-running the modal would sell
  // again, so the warning lives on the panel instead.
  const [stopLostWarning, setStopLostWarning] = useState<string | null>(null);

  const runPending = async () => {
    if (!pending) return;
    // Input validation happens before busy: a typo should not round-trip.
    if (pending.kind === "move-stop") {
      const price = Number(pending.stopPrice);
      if (!Number.isFinite(price) || price <= 0) {
        setActionError("Enter a valid stop price.");
        return;
      }
    }
    if (pending.kind === "partial") {
      const qty = Math.floor(Number(pending.qty));
      if (!Number.isFinite(qty) || qty <= 0 || qty >= pending.positionQty) {
        setActionError(`Enter a quantity between 1 and ${pending.positionQty - 1}.`);
        return;
      }
    }
    setBusy(true);
    try {
      if (pending.kind === "cancel") {
        await cancel(pending.id);
      } else if (pending.kind === "close") {
        await close(pending.symbol);
      } else if (pending.kind === "move-stop") {
        await moveStop(pending.id, pending.symbol, Number(pending.stopPrice));
      } else {
        const result = await close(pending.symbol, Math.floor(Number(pending.qty)));
        setStopLostWarning(
          result.order.stop_lost
            ? `${pending.symbol}: part sold, but the stop for the remainder could NOT be re-armed. ` +
              "Check the Orders tab and place a new stop by hand."
            : null,
        );
      }
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

  const count =
    tab === "positions"
      ? positions.length
      : tab === "orders"
        ? ordersView === "filled"
          ? orderHistory.fills.length
          : ordersView === "trades"
            ? tradeHistory.trades.length
            : orders.length
        : 0;

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
        {tab !== "account" && tab !== "balance" && (
          <span className="widget-count">{count}</span>
        )}
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
          <>
            {stopLostWarning && (
              <p className="order-rejection">
                {stopLostWarning}{" "}
                <button type="button" className="row-action" onClick={() => setStopLostWarning(null)}>
                  Dismiss
                </button>
              </p>
            )}
            <PositionsTable
              positions={positions}
              orders={orders}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={onSelectSymbol}
              onAction={setPending}
            />
          </>
        ) : tab === "orders" ? (
          <OrdersPanel
            view={ordersView}
            onViewChange={setOrdersView}
            orders={orders}
            history={orderHistory}
            trades={tradeHistory}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
            onCancelOrder={(id, symbol) => setPending({ kind: "cancel", id, symbol })}
          />
        ) : tab === "balance" ? (
          <BalancePanel
            range={balanceRange}
            onRangeChange={setBalanceRange}
            history={balance.history}
            loading={balance.loading}
            error={balance.error}
          />
        ) : (
          <AccountPanel account={account} paper={paper} tradingEnabled={tradingEnabled} />
        )}
      </div>

      <Modal
        open={pending !== null}
        title={
          pending?.kind === "cancel"
            ? "Cancel order"
            : pending?.kind === "move-stop"
              ? pending.breakeven
                ? "Stop to break-even"
                : "Move stop"
              : pending?.kind === "partial"
                ? "Sell part"
                : "Close position"
        }
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
          ) : pending?.kind === "move-stop" ? (
            <>
              <p className="order-confirm-line">
                {pending.breakeven ? (
                  <>
                    Move the <strong>{pending.symbol}</strong> stop to the entry price?
                  </>
                ) : (
                  <>
                    Move the <strong>{pending.symbol}</strong> stop to:
                  </>
                )}
              </p>
              <label className="order-confirm-line">
                Stop price{" "}
                <input
                  type="number"
                  step="0.01"
                  value={pending.stopPrice}
                  onChange={(e) => setPending({ ...pending, stopPrice: e.target.value })}
                />
              </label>
            </>
          ) : pending?.kind === "partial" ? (
            <>
              <p className="order-confirm-line">
                Sell part of <strong>{pending.symbol}</strong> ({pending.positionQty} held) at
                market?
              </p>
              <label className="order-confirm-line">
                Shares{" "}
                <input
                  type="number"
                  step="1"
                  min="1"
                  max={pending.positionQty - 1}
                  value={pending.qty}
                  onChange={(e) => setPending({ ...pending, qty: e.target.value })}
                />
                {[25, 50].map((pct) => (
                  <button
                    key={pct}
                    type="button"
                    className="timeframe-button"
                    onClick={() =>
                      setPending({
                        ...pending,
                        qty: String(Math.max(1, Math.floor((pending.positionQty * pct) / 100))),
                      })
                    }
                  >
                    {pct}%
                  </button>
                ))}
              </label>
              <p className="order-confirm-line order-confirm-note">
                The position&apos;s working exits are cancelled for the sale and re-armed for the
                remaining shares at their old prices -- the stop stays a stop, just smaller.
              </p>
            </>
          ) : (
            <>
              <p className="order-confirm-line">
                Close the entire <strong>{pending?.symbol}</strong> position at market?
              </p>
              {pending && orders.some((o) => o.symbol === pending.symbol) && (
                <p className="order-confirm-line order-confirm-note">
                  Any working order on {pending.symbol} will be cancelled first. Alpaca counts
                  shares held by a resting stop as unavailable, so the close cannot go through
                  while it is live.
                </p>
              )}
            </>
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
              {busy
                ? "Working"
                : pending?.kind === "cancel"
                  ? "Cancel order"
                  : pending?.kind === "move-stop"
                    ? "Move stop"
                    : pending?.kind === "partial"
                      ? "Sell shares"
                      : "Close position"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

type PendingAction =
  | { kind: "cancel"; id: string; symbol: string }
  | { kind: "close"; symbol: string }
  /** Input fields live as strings on the action so the modal can bind them
   * as controlled inputs; parsed and validated in runPending. */
  | { kind: "move-stop"; id: string; symbol: string; stopPrice: string; breakeven: boolean }
  | { kind: "partial"; symbol: string; qty: string; positionQty: number };

function PositionsTable({
  positions,
  orders,
  selectedSymbol,
  onSelectSymbol,
  onAction,
}: {
  positions: Position[];
  /** The working orders, so each row can show its own exits. Alpaca keeps
   * take-profit and stop-loss as separate orders, never on the position --
   * see exitsForPosition. */
  orders: Order[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onAction: (action: PendingAction) => void;
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
          <th>TP</th>
          <th>SL</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => {
          const pl = signedMoney(p.unrealized_pl);
          const plpc = signedPct(p.unrealized_plpc);
          const exits = exitsForPosition(p, orders);
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
              <td>{exits.takeProfit === null ? "—" : exits.takeProfit.toFixed(2)}</td>
              <td>
                {exits.stopLoss === null || exits.stopOrderId === null ? (
                  /* Called out rather than left as a bare em dash: a missing
                     take-profit only forgoes an exit price, a missing stop
                     means nothing closes this position on the way down. */
                  <span className="badge-no-stop" title="No stop order is working for this position">
                    NO STOP
                  </span>
                ) : (
                  /* The price is the edit control: clicking it opens the
                     move-stop dialog prefilled with where the stop is now. */
                  <button
                    type="button"
                    className="row-action"
                    title="Move this stop to a new price"
                    onClick={(e) => {
                      e.stopPropagation();
                      onAction({
                        kind: "move-stop",
                        id: exits.stopOrderId!,
                        symbol: p.symbol,
                        stopPrice: exits.stopLoss!.toFixed(2),
                        breakeven: false,
                      });
                    }}
                  >
                    {exits.stopLoss.toFixed(2)}
                  </button>
                )}
              </td>
              <td>
                <div className="row-actions">
                  <button
                    type="button"
                    className="row-action"
                    disabled={exits.stopOrderId === null}
                    title={
                      exits.stopOrderId === null
                        ? "No stop to move -- place one first"
                        : "Move the stop to the entry price"
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      if (exits.stopOrderId === null) return;
                      onAction({
                        kind: "move-stop",
                        id: exits.stopOrderId,
                        symbol: p.symbol,
                        stopPrice: String(num(p.avg_entry_price) ?? ""),
                        breakeven: true,
                      });
                    }}
                  >
                    BE
                  </button>
                  <button
                    type="button"
                    className="row-action"
                    disabled={(num(p.qty) ?? 0) < 2}
                    title="Sell part of the position; exits are re-armed for the rest"
                    onClick={(e) => {
                      e.stopPropagation();
                      const positionQty = Math.floor(Math.abs(num(p.qty) ?? 0));
                      onAction({
                        kind: "partial",
                        symbol: p.symbol,
                        // Half by default -- the SCALE_OUT the strategies run.
                        qty: String(Math.max(1, Math.floor(positionQty / 2))),
                        positionQty,
                      });
                    }}
                  >
                    Sell…
                  </button>
                  <button
                    type="button"
                    className="row-action"
                    onClick={(e) => {
                      e.stopPropagation();
                      onAction({ kind: "close", symbol: p.symbol });
                    }}
                  >
                    Close
                  </button>
                </div>
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

const FILL_TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function fillTime(order: Order): string {
  const stamp = order.filled_at ?? order.submitted_at ?? order.created_at;
  if (!stamp) return "—";
  const parsed = Date.parse(stamp);
  return Number.isFinite(parsed) ? FILL_TIME_FORMAT.format(new Date(parsed)) : "—";
}

/** The Orders tab: working orders you can still cancel, or the fills that
 * already happened. Split rather than merged, because the two want different
 * columns and only one of them has an action. */
function OrdersPanel({
  view,
  onViewChange,
  orders,
  history,
  trades,
  selectedSymbol,
  onSelectSymbol,
  onCancelOrder,
}: {
  view: OrdersView;
  onViewChange: (view: OrdersView) => void;
  orders: Order[];
  history: { fills: Order[]; loading: boolean; error: string | null };
  trades: TradesState;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onCancelOrder: (id: string, symbol: string) => void;
}) {
  return (
    <div className="trading-subview">
      <div className="trading-subview-bar">
        <div className="timeframe-selector">
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={view === "working"}
            onClick={() => onViewChange("working")}
          >
            Working
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={view === "filled"}
            onClick={() => onViewChange("filled")}
          >
            Filled
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={view === "trades"}
            onClick={() => onViewChange("trades")}
            title="Fills paired into round trips: what each closed position made or lost."
          >
            Trades
          </button>
        </div>
      </div>
      <div className="trading-subview-body">
        {view === "working" ? (
          <OrdersTable
            orders={orders}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
            onCancelOrder={onCancelOrder}
          />
        ) : view === "trades" ? (
          trades.error ? (
            <div className="widget-error">{trades.error}</div>
          ) : trades.loading && trades.trades.length === 0 ? (
            <div className="widget-empty">Loading trades&hellip;</div>
          ) : (
            <TradesTable
              trades={trades.trades}
              summary={trades.summary}
              openSymbols={trades.openSymbols}
              selectedSymbol={selectedSymbol}
              onSelectSymbol={onSelectSymbol}
            />
          )
        ) : history.error ? (
          <div className="widget-error">{history.error}</div>
        ) : history.loading && history.fills.length === 0 ? (
          <div className="widget-empty">Loading fills&hellip;</div>
        ) : (
          <FillsTable
            fills={history.fills}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
          />
        )}
      </div>
    </div>
  );
}

/** Completed fills, newest first. No action column: a fill is a record of
 * something that already happened, and there is nothing to do to it. */
function FillsTable({
  fills,
  selectedSymbol,
  onSelectSymbol,
}: {
  fills: Order[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}) {
  if (fills.length === 0) {
    return <div className="widget-empty">No fills yet.</div>;
  }
  return (
    <table className="performance-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Price</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((o) => {
          const qty = num(o.filled_qty);
          const price = num(o.filled_avg_price);
          return (
            <tr
              key={o.id}
              aria-selected={o.symbol === selectedSymbol}
              onClick={() => onSelectSymbol(o.symbol)}
            >
              <td>{fillTime(o)}</td>
              <td className="symbol-cell">{o.symbol}</td>
              <td>{o.side}</td>
              <td>{qty ?? "—"}</td>
              <td>{money(o.filled_avg_price)}</td>
              <td>{qty !== null && price !== null ? formatPrice(qty * price) : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function tradeTime(stamp: string): string {
  const parsed = Date.parse(stamp);
  return Number.isFinite(parsed) ? FILL_TIME_FORMAT.format(new Date(parsed)) : "—";
}

/** Closed round trips, newest first, with the totals underneath. R is the
 * same unit the strategy backtests report in, so a live week can be read
 * against a backtest of the same rule; a trade placed without a stop has
 * no R and shows a dash rather than a number that would mean nothing. */
function TradesTable({
  trades,
  summary,
  openSymbols,
  selectedSymbol,
  onSelectSymbol,
}: {
  trades: Trade[];
  summary: TradeSummary | null;
  openSymbols: string[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}) {
  if (trades.length === 0) {
    return (
      <div className="widget-empty">
        No closed trades yet.
        {openSymbols.length > 0 ? ` Still open: ${openSymbols.join(", ")}.` : ""}
      </div>
    );
  }
  const total = summary ? signedNumber(summary.total_pnl, 2) : null;
  const avgR = summary ? signedNumber(summary.avg_r, 2, "R") : null;
  return (
    <div className="trading-subview">
      <div className="trading-subview-body">
        <table className="performance-table">
          <thead>
            <tr>
              <th>Closed</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>P&amp;L</th>
              <th>%</th>
              <th title="P&L in units of the initial risk (entry to the stop the trade was placed with).">
                R
              </th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => {
              const pnl = signedNumber(t.pnl, 2);
              const pct = signedNumber(t.pnl_pct, 2, "%");
              const r = signedNumber(t.r_multiple, 2, "R");
              return (
                <tr
                  key={t.id}
                  aria-selected={t.symbol === selectedSymbol}
                  onClick={() => onSelectSymbol(t.symbol)}
                  title={`Opened ${tradeTime(t.opened_at)} · ${t.fill_count} fills${
                    t.initial_stop !== null ? ` · initial stop ${t.initial_stop.toFixed(2)}` : " · no stop"
                  }`}
                >
                  <td>{tradeTime(t.closed_at)}</td>
                  <td className="symbol-cell">{t.symbol}</td>
                  <td>{t.side}</td>
                  <td>{t.qty.toLocaleString()}</td>
                  <td>{formatPrice(t.entry_avg)}</td>
                  <td>{formatPrice(t.exit_avg)}</td>
                  <td className={pnl.cls}>{pnl.text}</td>
                  <td className={pct.cls}>{pct.text}</td>
                  <td className={r.cls}>{r.text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {summary && total && avgR && (
        <div className="trading-balance-summary">
          <span className="trading-balance-figure">
            <span className="trading-account-label">Total</span>
            <strong className={total.cls}>{total.text}</strong>
          </span>
          <span className="trading-balance-figure">
            <span className="trading-account-label">Win rate</span>
            <strong>
              {summary.win_rate === null ? "—" : `${summary.win_rate.toFixed(0)}%`}
              {` (${summary.wins}W / ${summary.losses}L)`}
            </strong>
          </span>
          <span className="trading-balance-figure">
            <span className="trading-account-label">Avg win / loss</span>
            <strong>
              {summary.avg_win === null ? "—" : formatPrice(summary.avg_win)}
              {" / "}
              {summary.avg_loss === null ? "—" : formatPrice(summary.avg_loss)}
            </strong>
          </span>
          <span className="trading-balance-figure" title="Gross wins divided by gross losses.">
            <span className="trading-account-label">Profit factor</span>
            <strong>{summary.profit_factor === null ? "—" : summary.profit_factor.toFixed(2)}</strong>
          </span>
          <span
            className="trading-balance-figure"
            title={`Mean R over the ${summary.r_count} trade(s) that had an initial stop -- the expectancy the strategy backtests report.`}
          >
            <span className="trading-account-label">Expectancy</span>
            <strong className={avgR.cls}>{avgR.text}</strong>
          </span>
          {openSymbols.length > 0 && (
            <span className="trading-balance-figure">
              <span className="trading-account-label">Still open</span>
              <strong>{openSymbols.join(", ")}</strong>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** The Balance tab: the equity curve over a chosen range, with what it adds
 * up to underneath it. */
function BalancePanel({
  range,
  onRangeChange,
  history,
  loading,
  error,
}: {
  range: BalanceRange;
  onRangeChange: (range: BalanceRange) => void;
  history: PortfolioHistoryResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const points = history?.points ?? [];
  const change = history?.change ?? null;
  const changePct = history?.change_pct ?? null;
  const changeText =
    change === null
      ? "—"
      : `${change > 0 ? "+" : ""}${formatPrice(change)}${
          changePct === null ? "" : ` (${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%)`
        }`;

  return (
    <div className="trading-subview">
      <div className="trading-subview-bar">
        <div className="timeframe-selector">
          {BALANCE_RANGES.map((r) => (
            <button
              key={r}
              type="button"
              className="timeframe-button"
              aria-pressed={range === r}
              onClick={() => onRangeChange(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <div className="trading-balance-chart">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : loading ? (
          <div className="widget-empty">Loading balance&hellip;</div>
        ) : points.length < 2 ? (
          /* One point draws no line, and an account younger than the range
             legitimately has only a handful. Saying so beats an empty pane. */
          <div className="widget-empty">Not enough history yet for this range.</div>
        ) : (
          <BalanceChart points={points} daily={history?.timeframe === DAILY_TIMEFRAME} />
        )}
      </div>

      <div className="trading-balance-summary">
        <span className="trading-balance-figure">
          <span className="trading-account-label">Equity</span>
          <strong>{history?.end_equity == null ? "—" : formatPrice(history.end_equity)}</strong>
        </span>
        <span className="trading-balance-figure">
          <span className="trading-account-label">Period P&amp;L</span>
          <strong
            className={change === null || change === 0 ? "" : change > 0 ? "delta-up" : "delta-down"}
          >
            {changeText}
          </strong>
        </span>
      </div>
    </div>
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
