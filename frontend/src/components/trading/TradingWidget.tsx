import { useEffect, useMemo, useState } from "react";

import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { resetSimAccount } from "../../api/http";
import { useTradingContext } from "../../context/TradingContext";
import { useBalanceHistory } from "../../hooks/useBalanceHistory";
import { useOrderHistory } from "../../hooks/useOrderHistory";
import { useTrades } from "../../hooks/useTrades";
import type { TradesState } from "../../hooks/useTrades";
import type {
  Account,
  BalanceRange,
  Order,
  Position,
  PortfolioHistoryResponse,
  Trade,
  TradeBucket,
  TradeSummary,
  TradesRange,
} from "../../types/trading";
import { exitsForPosition, num } from "../../types/trading";
import { formatPrice } from "../../utils/format";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "./LiveConfirmField";
import { chartSymbolOf, formatLeg } from "../../utils/occ";
import { BalanceChart } from "./BalanceChart";
import { OrderTicket } from "./OrderTicket";

type Tab = "ticket" | "positions" | "orders" | "balance" | "account";

/** Working orders and completed fills are both "orders", but one is a thing
 * you can still act on and the other is a record. Trades are the fills
 * paired back into round trips -- the record that answers "which position
 * made or lost what", which neither of the other two can. Same tab, three
 * views. */
type OrdersView = "working" | "filled" | "trades";

// DAS script #2 ("Move Stop to Breakeven Plus Offset") and #11/#13 ("Sell/
// Cover % of Position") -- both DAS defaults, adjustable here.
const BREAKEVEN_OFFSET = 0.05;
const SCALE_OUT_FRACTION = 0.5;

const TABS: { id: Tab; label: string }[] = [
  { id: "ticket", label: "Ticket" },
  { id: "positions", label: "Positions" },
  { id: "orders", label: "Orders" },
  { id: "balance", label: "Balance" },
  { id: "account", label: "Account" },
];

const BALANCE_RANGES: BalanceRange[] = ["1D", "1W", "1M", "3M", "1Y", "ALL"];

/** Calendar periods for the Filled and Trades views. Labelled as periods
 * ("Day", not "1D") because they are calendar windows in ET -- this
 * session, this week from Monday, this month -- not rolling ones like the
 * balance curve's. */
const TRADE_RANGES: { id: TradesRange; label: string; title: string }[] = [
  { id: "day", label: "Day", title: "Today's session (ET)." },
  { id: "week", label: "Week", title: "This week, from Monday (ET)." },
  { id: "month", label: "Month", title: "This month, from the 1st (ET)." },
  { id: "all", label: "All", title: "Everything on record." },
];

/** YYYY-MM-DD of an instant in ET -- the calendar a session lives in. A
 * fill at 19:30 ET belongs to that day even though UTC has rolled over.
 * Mirrors trades.period_start on the backend, which applies the same
 * calendar to the Trades view server-side. */
const ET_DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function etDate(ms: number): string {
  const parts = ET_DATE.formatToParts(new Date(ms));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** First ET date of the period containing `nowMs`, or null for "all". */
function periodStartDate(range: TradesRange, nowMs: number): string | null {
  if (range === "all") return null;
  const today = etDate(nowMs);
  if (range === "day") return today;
  if (range === "month") return `${today.slice(0, 8)}01`;
  // Back to Monday. Noon UTC of the ET date keeps the arithmetic clear of
  // any DST edge when stepping days.
  const d = new Date(`${today}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

function inPeriod(stamp: string | null, range: TradesRange, nowMs: number): boolean {
  const start = periodStartDate(range, nowMs);
  if (start === null) return true;
  if (!stamp) return false;
  const ms = Date.parse(stamp);
  return Number.isFinite(ms) && etDate(ms) >= start;
}

function periodNoun(range: TradesRange): string {
  return range === "day" ? "today" : range === "week" ? "this week" : range === "month" ? "this month" : "yet";
}

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
 * returns -- already numbers, already in the unit shown. Exported for
 * TradeJournalWidget, which shows the same P&L/R figures against the same
 * trade list. */
/** A multi-leg (spread) parent order has no symbol of its own; label it
 * by its legs so the Orders tab can still say what it is. */
/** The chart symbol behind an order: the stock itself, or an option
 * order's underlying (an MLEG parent has no symbol, so take a leg's). */
function orderChartSymbol(order: Order): string | null {
  const symbol = order.symbol ?? order.legs?.find((leg) => leg.symbol)?.symbol ?? null;
  return symbol ? chartSymbolOf(symbol) : null;
}

function orderLabel(order: Order): string {
  if (order.symbol) return order.symbol;
  const legs = (order.legs ?? []).map((leg) => (leg.symbol ? formatLeg(leg.symbol) : "?"));
  return legs.length > 0 ? legs.join(" / ") : "multi-leg";
}

export function signedNumber(
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
  /** "simulation" routes every trading call in this widget's subtree to the
   * local sim broker instead of the real account -- see api/tradingMode.ts.
   * Purely a display/labelling concern here; the actual routing happens
   * transparently in api/http.ts. */
  mode: TradingMode;
}

/** Account state for the connected Alpaca account: open positions, working
 * orders and the balance line. Read-only for now -- order entry lands in the
 * next milestone, behind TRADING_ENABLED and a paper-account check. */
export function TradingWidget({ selectedSymbol, onSelectSymbol, mode }: TradingWidgetProps) {
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
  } = useTradingContext();
  const [tab, setTab] = useState<Tab>("ticket");
  // The typed LIVE for the confirm dialog below; cleared with the dialog.
  const [liveTyped, setLiveTyped] = useState("");
  const badge = modeBadge(mode);
  const [ordersView, setOrdersView] = useState<OrdersView>("working");
  const [balanceRange, setBalanceRange] = useState<BalanceRange>("1M");
  // Both hooks are held here rather than inside their panels so the header
  // count can read them, and both stay idle until their tab is open.
  // One period for both history views, so switching Filled <-> Trades keeps
  // looking at the same days. Defaults to today: the question the tab is
  // opened for most often is "how is the session going".
  const [tradesRange, setTradesRange] = useState<TradesRange>("day");
  const orderHistory = useOrderHistory(tab === "orders" && ordersView === "filled");
  const tradeHistory = useTrades(tab === "orders" && ordersView === "trades", tradesRange);
  const balance = useBalanceHistory(balanceRange, tab === "balance");
  // The fills view is narrowed client-side -- the broker's closed-orders
  // query has no calendar filter, and the list is already in hand.
  const periodFills = useMemo(() => {
    const now = Date.now();
    return orderHistory.fills.filter((o) =>
      inPeriod(o.filled_at ?? o.submitted_at ?? o.created_at, tradesRange, now),
    );
  }, [orderHistory.fills, tradesRange]);
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

  // Simulation Mode's own destructive action -- kept as a separate confirm
  // rather than folded into PendingAction/runPending above, since those are
  // built around a position/order id this has neither of.
  const [resetConfirming, setResetConfirming] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const runReset = async () => {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetSimAccount();
      setResetConfirming(false);
      afterAction();
    } catch (err: unknown) {
      setResetError(err instanceof Error ? err.message : String(err));
    } finally {
      setResetBusy(false);
    }
  };

  // Instant-fire position/order hotkeys: F flattens the selected symbol's
  // position, C cancels every working order, 0 moves its stop to
  // breakeven, Shift+0 to breakeven plus a small offset (DAS script #2),
  // X scales the position out by SCALE_OUT_FRACTION (DAS's "sell/cover %"
  // scripts #11-14). Same actions PositionsTable's BE/Sell.../Close buttons
  // already trigger via setPending + the confirm Modal below -- these call
  // close/cancel/moveStop directly instead, no dialog. There is deliberately
  // no bulk-cancel endpoint on the backend (see close_position's docstring
  // in routers/trading.py), so C loops the orders already on screen rather
  // than calling a new server-side "cancel everything" route.
  const [instantBusy, setInstantBusy] = useState(false);
  const [instantError, setInstantError] = useState<string | null>(null);

  // Shared by the F/C/0/Shift+0/X hotkeys below and their button-row
  // equivalents in the JSX further down.
  const runInstantAction = async (
    kind: "flatten" | "cancel-all" | "breakeven" | "breakeven-offset" | "scale-out",
  ) => {
    if (instantBusy) return;
    // Real money never moves on a single keypress: every live action goes
    // through a dialog that asks for the typed confirmation.
    if (mode === "live") {
      setInstantError("Instant actions are off in Live mode -- use the row actions and confirm.");
      return;
    }
    setInstantBusy(true);
    setInstantError(null);
    try {
      if (kind === "flatten") {
        if (!selectedSymbol) return;
        if (!positions.some((p) => p.symbol === selectedSymbol)) return;
        await close(selectedSymbol);
      } else if (kind === "cancel-all") {
        if (orders.length === 0) return;
        await Promise.all(orders.map((o) => cancel(o.id)));
      } else if (kind === "breakeven" || kind === "breakeven-offset") {
        if (!selectedSymbol) return;
        const position = positions.find((p) => p.symbol === selectedSymbol);
        if (!position) return;
        const exits = exitsForPosition(position, orders);
        const entry = num(position.avg_entry_price);
        if (exits.stopOrderId === null || entry === null) return;
        const offset =
          kind === "breakeven-offset" ? (position.side === "short" ? -BREAKEVEN_OFFSET : BREAKEVEN_OFFSET) : 0;
        await moveStop(exits.stopOrderId, selectedSymbol, entry + offset);
      } else {
        if (!selectedSymbol) return;
        const position = positions.find((p) => p.symbol === selectedSymbol);
        if (!position) return;
        const positionQty = Math.floor(Math.abs(num(position.qty) ?? 0));
        if (positionQty < 2) return;
        const qty = Math.max(1, Math.floor(positionQty * SCALE_OUT_FRACTION));
        await close(selectedSymbol, qty);
      }
    } catch (err: unknown) {
      setInstantError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstantBusy(false);
    }
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = document.activeElement;
      const isTyping =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (isTyping || instantBusy || pending !== null) return;

      const key = e.key.toLowerCase();
      if (key === "0") {
        e.preventDefault();
        void runInstantAction(e.shiftKey ? "breakeven-offset" : "breakeven");
        return;
      }
      if (key === "f") {
        e.preventDefault();
        void runInstantAction("flatten");
      } else if (key === "c") {
        e.preventDefault();
        void runInstantAction("cancel-all");
      } else if (key === "x") {
        e.preventDefault();
        void runInstantAction("scale-out");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [instantBusy, pending, runInstantAction]);

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
    if (!liveConfirmed(mode, liveTyped)) {
      setActionError("Type LIVE to confirm a real-money action.");
      return;
    }
    const confirm = mode === "live" ? liveTyped.trim() : undefined;
    setBusy(true);
    try {
      if (pending.kind === "cancel") {
        await cancel(pending.id, confirm);
      } else if (pending.kind === "close") {
        await close(pending.symbol, undefined, confirm);
      } else if (pending.kind === "move-stop") {
        await moveStop(pending.id, pending.symbol, Number(pending.stopPrice), confirm);
      } else {
        const result = await close(pending.symbol, Math.floor(Number(pending.qty)), confirm);
        setStopLostWarning(
          result.order.stop_lost
            ? `${pending.symbol}: part sold, but the stop for the remainder could NOT be re-armed. ` +
              "Check the Orders tab and place a new stop by hand."
            : null,
        );
      }
      setPending(null);
      setActionError(null);
      setLiveTyped("");
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
          ? periodFills.length
          : ordersView === "trades"
            ? tradeHistory.trades.length
            : orders.length
        : 0;

  // Drives the instant-fire button row's disabled states -- same reads
  // runInstantAction itself does, computed once here instead of per-button.
  const selectedPosition = selectedSymbol
    ? (positions.find((p) => p.symbol === selectedSymbol) ?? null)
    : null;
  const selectedExits = selectedPosition ? exitsForPosition(selectedPosition, orders) : null;

  return (
    <div className={`widget trading-widget${mode === "live" ? " live-frame" : ""}`}>
      <div className="widget-header">
        <h2>Trading</h2>
        {/* The single most important thing on this panel is whether the money
            is real. Shown always, not only when it is live. Simulation is a
            client-known state layered on top -- paper/live still describes
            whichever real account the backend's credentials point at. */}
        <span className={`trading-mode-badge ${badge.className}`}>{badge.label}</span>
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

      <div
        className="timeframe-selector instant-fire-row"
        title="Fires immediately -- no confirmation dialog."
      >
        <button
          type="button"
          className="timeframe-button instant-fire-sell"
          disabled={!selectedPosition || instantBusy}
          onClick={() => void runInstantAction("flatten")}
          title={
            selectedPosition ? "Hotkey: F" : "Select a symbol with an open position"
          }
        >
          Flatten
        </button>
        <button
          type="button"
          className="timeframe-button instant-fire-sell"
          disabled={orders.length === 0 || instantBusy}
          onClick={() => void runInstantAction("cancel-all")}
          title={orders.length === 0 ? "No working orders" : "Hotkey: C"}
        >
          Cancel all
        </button>
        <button
          type="button"
          className="timeframe-button"
          disabled={!selectedExits || selectedExits.stopOrderId === null || instantBusy}
          onClick={() => void runInstantAction("breakeven")}
          title={
            selectedExits && selectedExits.stopOrderId !== null
              ? "Hotkey: 0"
              : "Select a symbol with a working stop"
          }
        >
          Stop to BE
        </button>
        <button
          type="button"
          className="timeframe-button"
          disabled={!selectedExits || selectedExits.stopOrderId === null || instantBusy}
          onClick={() => void runInstantAction("breakeven-offset")}
          title={
            selectedExits && selectedExits.stopOrderId !== null
              ? `Hotkey: Shift+0 -- breakeven +/- $${BREAKEVEN_OFFSET.toFixed(2)}`
              : "Select a symbol with a working stop"
          }
        >
          BE+offset
        </button>
        <button
          type="button"
          className="timeframe-button instant-fire-sell"
          disabled={!selectedPosition || Math.floor(Math.abs(num(selectedPosition.qty) ?? 0)) < 2 || instantBusy}
          onClick={() => void runInstantAction("scale-out")}
          title={
            selectedPosition
              ? `Hotkey: X -- sell/cover ${SCALE_OUT_FRACTION * 100}% at market`
              : "Select a symbol with an open position"
          }
        >
          Scale out
        </button>
      </div>

      {instantError && (
        <div className="order-rejection" role="status">
          {instantError}{" "}
          <button type="button" className="row-action" onClick={() => setInstantError(null)}>
            Dismiss
          </button>
        </div>
      )}

      <div className="widget-body">
        {error ? (
          <div className="widget-error">{error}</div>
        ) : loading ? (
          <div className="widget-empty">Loading account…</div>
        ) : tab === "ticket" ? (
          <OrderTicket
            symbol={selectedSymbol}
            defaultRiskPct={defaultRiskPct}
            account={account}
            position={positions.find((p) => p.symbol === selectedSymbol) ?? null}
            orders={orders}
            onSubmitted={afterAction}
            mode={mode}
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
            history={{ ...orderHistory, fills: periodFills }}
            trades={tradeHistory}
            range={tradesRange}
            onRangeChange={setTradesRange}
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
          <AccountPanel
            account={account}
            paper={paper}
            tradingEnabled={tradingEnabled}
            mode={mode}
            onRequestReset={() => setResetConfirming(true)}
          />
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
          setLiveTyped("");
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
          <p className="order-confirm-mode">{badge.confirmLine}</p>
          <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
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
              className={`generate-button${mode === "live" ? " live-action" : ""}`}
              disabled={busy || !liveConfirmed(mode, liveTyped)}
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

      <Modal
        open={resetConfirming}
        title="Reset simulation"
        onClose={() => {
          setResetConfirming(false);
          setResetError(null);
        }}
      >
        <div className="order-confirm">
          <p className="order-confirm-line">
            Clear every simulated position, working order and trade, and reset cash back to the
            starting balance?
          </p>
          <p className="order-confirm-line order-confirm-note">This cannot be undone.</p>
          {resetError && <p className="order-rejection">{resetError}</p>}
          <div className="order-confirm-actions">
            <button
              type="button"
              className="timeframe-button"
              onClick={() => {
                setResetConfirming(false);
                setResetError(null);
              }}
            >
              Keep it
            </button>
            <button
              type="button"
              className="generate-button"
              disabled={resetBusy}
              onClick={() => void runReset()}
            >
              {resetBusy ? "Working" : "Reset simulation"}
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
  // Option legs are grouped into spreads by the Options widget; here they
  // would read as unrelated single contracts.
  const equityPositions = positions.filter((p) => (p.asset_class ?? "us_equity") !== "us_option");
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
        {equityPositions.map((p) => {
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
            aria-selected={orderChartSymbol(o) === selectedSymbol}
            onClick={() => {
              const symbol = orderChartSymbol(o);
              if (symbol) onSelectSymbol(symbol);
            }}
          >
            <td className="symbol-cell">{orderLabel(o)}</td>
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
                  onCancelOrder(o.id, orderLabel(o));
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
  range,
  onRangeChange,
  selectedSymbol,
  onSelectSymbol,
  onCancelOrder,
}: {
  view: OrdersView;
  onViewChange: (view: OrdersView) => void;
  orders: Order[];
  history: { fills: Order[]; loading: boolean; error: string | null };
  trades: TradesState;
  range: TradesRange;
  onRangeChange: (range: TradesRange) => void;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onCancelOrder: (id: string, symbol: string) => void;
}) {
  return (
    <div className="trading-subview">
      <div className="trading-subview-bar trading-subview-bar-split">
        {/* Working orders are whatever is live right now; a period only
            means something for the two history views. */}
        {view !== "working" && (
          <div className="timeframe-selector">
            {TRADE_RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                className="timeframe-button"
                aria-pressed={range === r.id}
                onClick={() => onRangeChange(r.id)}
                title={r.title}
              >
                {r.label}
              </button>
            ))}
          </div>
        )}
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
              buckets={trades.buckets}
              range={range}
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
            range={range}
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
  range,
  selectedSymbol,
  onSelectSymbol,
}: {
  fills: Order[];
  range: TradesRange;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}) {
  if (fills.length === 0) {
    return <div className="widget-empty">No fills {periodNoun(range)}.</div>;
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
              onClick={() => o.symbol && onSelectSymbol(o.symbol)}
            >
              <td>{fillTime(o)}</td>
              <td className="symbol-cell">{orderLabel(o)}</td>
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

/** Exported for TradeJournalWidget -- same closed_at/opened_at timestamps,
 * same display convention. */
export function tradeTime(stamp: string): string {
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
  buckets,
  range,
  openSymbols,
  selectedSymbol,
  onSelectSymbol,
}: {
  trades: Trade[];
  summary: TradeSummary | null;
  buckets: TradeBucket[];
  range: TradesRange;
  openSymbols: string[];
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}) {
  // A day picked from the breakdown narrows the trade list to that date;
  // picking it again (or changing the period) lets go.
  const [dayFilter, setDayFilter] = useState<string | null>(null);
  useEffect(() => setDayFilter(null), [range]);
  const shown = useMemo(
    () => (dayFilter ? trades.filter((t) => etDate(Date.parse(t.closed_at)) === dayFilter) : trades),
    [trades, dayFilter],
  );

  if (trades.length === 0) {
    return (
      <div className="widget-empty">
        No closed trades {periodNoun(range)}.
        {openSymbols.length > 0 ? ` Still open: ${openSymbols.join(", ")}.` : ""}
      </div>
    );
  }
  const total = summary ? signedNumber(summary.total_pnl, 2) : null;
  const avgR = summary ? signedNumber(summary.avg_r, 2, "R") : null;
  // The breakdown earns its space once a period spans more than one day.
  const showBreakdown = range !== "day" && buckets.length > 1;
  return (
    <div className="trading-subview">
      <div className="trading-subview-body">
        {showBreakdown && (
          <table className="performance-table trading-breakdown">
            <thead>
              <tr>
                <th>Day</th>
                <th>Trades</th>
                <th>W / L</th>
                <th>P&amp;L</th>
                <th title="Running total through this day.">Cum.</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((b) => {
                const pnl = signedNumber(b.pnl, 2);
                const cum = signedNumber(b.cumulative_pnl, 2);
                return (
                  <tr
                    key={b.date}
                    aria-selected={b.date === dayFilter}
                    onClick={() => setDayFilter(dayFilter === b.date ? null : b.date)}
                    title="Click to show only this day's trades."
                  >
                    <td>{b.date}</td>
                    <td>{b.count}</td>
                    <td>
                      {b.wins} / {b.losses}
                    </td>
                    <td className={pnl.cls}>{pnl.text}</td>
                    <td className={cum.cls}>{cum.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
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
            {shown.map((t) => {
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
  mode,
  onRequestReset,
}: {
  account: Account | null;
  paper: boolean;
  tradingEnabled: boolean;
  mode: TradingMode;
  onRequestReset: () => void;
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
        {mode === "simulation"
          ? "Simulation Mode — a local practice account, priced off real live data. Never touches the real account."
          : paper
            ? "Simulated account — no real money is at risk."
            : "LIVE account. Order placement is refused by this build."}{" "}
        {mode !== "simulation" && `Order entry is ${tradingEnabled ? "enabled" : "disabled"} (TRADING_ENABLED).`}
      </p>
      {mode === "simulation" && (
        <button type="button" className="timeframe-button" onClick={onRequestReset}>
          Reset simulation
        </button>
      )}
    </div>
  );
}
