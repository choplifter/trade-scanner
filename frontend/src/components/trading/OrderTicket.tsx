import { useEffect, useRef, useState } from "react";

import { OrderRejectedError, previewOrder, submitOrder } from "../../api/http";
import { Modal } from "../common/Modal";
import { exitsForPosition, num } from "../../types/trading";
import type {
  Account,
  EntryOrderType,
  Order,
  OrderPreview,
  OrderTicketRequest,
  Position,
  TradingRejection,
} from "../../types/trading";
import { formatPrice } from "../../utils/format";

function money(value: string | null | undefined): string {
  const parsed = num(value);
  return parsed === null ? "—" : formatPrice(parsed);
}

type SizingMode = "shares" | "risk";

/** Which entry price each type carries. A stop-limit has both: the trigger
 * that activates it and the limit it then goes in at. */
const NEEDS_LIMIT: ReadonlySet<EntryOrderType> = new Set(["limit", "stop_limit"]);
const NEEDS_TRIGGER: ReadonlySet<EntryOrderType> = new Set(["stop", "stop_limit"]);

const ORDER_TYPES: { type: EntryOrderType; label: string; title: string }[] = [
  { type: "market", label: "Market", title: "Fills now at the current price." },
  {
    type: "limit",
    label: "Limit",
    title:
      "Buy at this price or lower / sell at this price or higher. A buy limit ABOVE the market fills immediately -- it does not wait for price to reach it.",
  },
  {
    type: "stop",
    label: "Stop",
    title:
      "Breakout entry: rests until price trades through the trigger, then fills at market. A buy stop sits above the market, a sell stop below.",
  },
  {
    type: "stop_limit",
    label: "Stop-limit",
    title:
      "Breakout entry with a cap: rests until price trades through the trigger, then goes in as a limit order.",
  },
];

/** Debounce: the preview is a round trip that also fetches the account, so
 * firing per keystroke would be wasteful and would make the displayed size
 * lag behind the field being typed into. */
const PREVIEW_DEBOUNCE_MS = 350;

function numberOrUndefined(value: string): number | undefined {
  if (value.trim() === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** crypto.randomUUID() only exists in a secure context (HTTPS or localhost)
 * -- opening the dashboard as http://<lan-hostname>:5173 from another
 * machine leaves it undefined, which used to throw inside openConfirm and
 * silently kill the click before the confirm dialog opened. getRandomValues
 * has no such restriction, so build a v4 UUID from that instead. */
function randomUUID(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

interface OrderTicketProps {
  symbol: string | null;
  defaultRiskPct: number;
  /** For the buying-power/equity context strip and the quick-% sizing
   * buttons (#1) -- read-only here, the ticket never mutates the account. */
  account: Account | null;
  /** The position already open on `symbol`, if any -- drives the "already
   * holding N @ price" context line and the existing-exit warning (#6). */
  position: Position | null;
  /** Paired with `position` via `exitsForPosition` for the existing-exit
   * warning (#6). */
  orders: Order[];
  /** Called after a successful submit so the positions/orders tables and the
   * account line refresh immediately rather than waiting for the next poll. */
  onSubmitted: () => void;
}

/** The order ticket. Sizes and prices through the server on every edit, so
 * what is shown is what the broker would receive -- the arithmetic is never
 * duplicated client-side, where it could drift from the ceilings that
 * actually gate a submit.
 *
 * Submit is gated twice over: the button only enables when the server says
 * can_submit (TRADING_ENABLED and a paper account), and the confirmation
 * dialog stands between the button and the order. */
export function OrderTicket({
  symbol,
  defaultRiskPct,
  account,
  position,
  orders,
  onSubmitted,
}: OrderTicketProps) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderType, setOrderType] = useState<EntryOrderType>("market");
  const [sizingMode, setSizingMode] = useState<SizingMode>("risk");

  const [qty, setQty] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  // The entry trigger of a stop / stop-limit order. Kept apart from
  // stopPrice below, which is the *protective* stop the risk sizing works
  // from -- a breakout ticket has both, and they mean opposite things.
  const [triggerPrice, setTriggerPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [riskPct, setRiskPct] = useState(String(defaultRiskPct));
  const [takeProfit, setTakeProfit] = useState("");
  // null means "whatever the server derives from the ticket" -- a protected
  // ticket defaults to gtc so its legs outlive the close. Clicking either
  // button pins the choice instead.
  const [timeInForce, setTimeInForce] = useState<"day" | "gtc" | null>(null);

  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [previewPending, setPreviewPending] = useState(false);
  const [rejection, setRejection] = useState<TradingRejection | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [placed, setPlaced] = useState<string | null>(null);

  // Advisory only -- doesn't affect canSubmit/disabledReason. Reset below
  // whenever the symbol changes, so switching to a different already-exited
  // position re-shows it rather than staying dismissed from a prior symbol.
  const [existingExitsDismissed, setExistingExitsDismissed] = useState(false);
  useEffect(() => {
    setExistingExitsDismissed(false);
  }, [symbol]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Minted once when the dialog opens, not per attempt: Alpaca rejects a
  // duplicate client_order_id, so retrying after a timeout resubmits the
  // *same* order rather than opening a second position. A server-generated
  // id would defeat that, since a retry would arrive with a new one.
  const clientOrderIdRef = useRef<string | null>(null);

  // Hotkeys: B/S for side, 1-4 for order type (matching ORDER_TYPES' order),
  // Enter to open the confirm dialog. Placed above the `if (!symbol) return`
  // below so this hook always runs -- referencing preview/submitting/
  // confirming state directly rather than the canSubmit/openConfirm consts
  // defined further down, which only exist once a symbol is selected.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = document.activeElement;
      const isTyping =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      // Modal.tsx already owns Escape on the confirm dialog; leave every key
      // alone while it's open rather than risk double-handling one of these.
      if (isTyping || confirming) return;

      if (e.key === "b" || e.key === "B") {
        setSide("buy");
      } else if (e.key === "s" || e.key === "S") {
        setSide("sell");
      } else if (e.key >= "1" && e.key <= String(ORDER_TYPES.length)) {
        setOrderType(ORDER_TYPES[Number(e.key) - 1].type);
      } else if (e.key === "Enter") {
        const canSubmitNow = Boolean(preview?.order && preview?.can_submit) && !submitting;
        if (!canSubmitNow) return;
        clientOrderIdRef.current = randomUUID();
        setPlaced(null);
        setConfirming(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirming, preview, submitting]);

  // Sanity bound for Risk %, not a hard broker limit -- the backend enforces
  // only gt=0 (no ceiling exists server-side), so this exists purely to
  // catch a fat-fingered value (50 typed for 0.5) before it prices as a real
  // order. Relative to the account's own default rather than a flat number,
  // with a 5% floor so a very low default doesn't make the guardrail trip on
  // ordinary values. Deliberately NOT keyed off preview.limits.default_risk_pct
  // -- that would put `missing` (an effect dependency) in a feedback loop
  // with the very preview the effect fetches: a blocked ticket clears
  // preview, which removes the reference, which unblocks it, which
  // re-fetches, forever.
  const riskPctCeiling = Math.max(5, defaultRiskPct * 5);
  const riskPctValue = numberOrUndefined(riskPct);

  // What the ticket still needs before it can be priced at all. Used both
  // to skip pointless preview requests and to say, on the button itself, why
  // nothing happens -- a control that silently refuses to act reads as
  // broken, which is exactly how the empty-stop case got reported.
  const missing: string | null = !symbol
    ? "Select a symbol"
    : NEEDS_TRIGGER.has(orderType) && !numberOrUndefined(triggerPrice)
      ? "Enter a trigger price — the order rests until price trades through it"
      : NEEDS_LIMIT.has(orderType) && !numberOrUndefined(limitPrice)
      ? "Enter a limit price"
      : sizingMode === "shares"
        ? !numberOrUndefined(qty)
          ? "Enter a quantity"
          : null
        : !numberOrUndefined(stopPrice)
          ? "Enter a stop price — it is what sizes the order"
          : riskPctValue === undefined
            ? "Enter a risk %"
            : riskPctValue > riskPctCeiling
              ? "Risk % looks too high — check the value before pricing"
              : null;

  useEffect(() => {
    if (!symbol) {
      setPreview(null);
      setRejection(null);
      return;
    }

    const ticket: OrderTicketRequest = {
      symbol,
      side,
      order_type: orderType,
      // Only when pinned. Left out, the server decides -- keeping that rule
      // in one place instead of restating it here where it could drift.
      ...(timeInForce ? { time_in_force: timeInForce } : {}),
      ...(NEEDS_LIMIT.has(orderType) ? { limit_price: numberOrUndefined(limitPrice) } : {}),
      ...(NEEDS_TRIGGER.has(orderType) ? { stop_price: numberOrUndefined(triggerPrice) } : {}),
      ...(takeProfit.trim() ? { take_profit_price: numberOrUndefined(takeProfit) } : {}),
      ...(sizingMode === "shares"
        ? { qty: numberOrUndefined(qty) }
        : {
            risk: {
              stop_price: numberOrUndefined(stopPrice) ?? 0,
              risk_pct_of_equity: numberOrUndefined(riskPct),
            },
          }),
    };

    // Don't ask the server to price a ticket that is obviously incomplete --
    // it would answer with a validation error the user hasn't earned yet.
    if (missing) {
      setPreview(null);
      setRejection(null);
      setPreviewPending(false);
      return;
    }

    if (timerRef.current) clearTimeout(timerRef.current);
    let cancelled = false;
    setPreviewPending(true);
    timerRef.current = setTimeout(() => {
      previewOrder(ticket)
        .then((result) => {
          if (cancelled) return;
          setPreview(result);
          setRejection(null);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setPreview(null);
          if (err instanceof OrderRejectedError) {
            setRejection(err.detail);
            setError(null);
          } else {
            setRejection(null);
            setError(err instanceof Error ? err.message : String(err));
          }
        })
        .finally(() => {
          if (cancelled) return;
          setPreviewPending(false);
        });
    }, PREVIEW_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [
    symbol,
    side,
    orderType,
    sizingMode,
    qty,
    limitPrice,
    triggerPrice,
    stopPrice,
    riskPct,
    takeProfit,
    missing,
  ]);

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to build an order.</div>;
  }

  const order = preview?.order;
  // Reference price for the quick-% sizing buttons (#1) -- never fires an
  // extra preview request just to have a price; falls back to the position's
  // last known price, then gives up and disables the buttons.
  const sizingPrice = order?.entry_reference ?? num(position?.current_price) ?? null;
  const buyingPower = num(account?.buying_power);
  const exits = position ? exitsForPosition(position, orders) : null;

  // A synchronous, client-side "≈ N shares" estimate for risk mode, shown
  // while the authoritative server-priced qty in the preview panel is
  // stale or still in flight -- it must read as visually distinct from that
  // qty since the two can disagree during previewPending.
  const riskEntryRef = num(position?.current_price) ?? order?.entry_reference ?? null;
  const estimatedRiskShares = (() => {
    if (sizingMode !== "risk") return null;
    const equity = num(account?.equity);
    const stop = numberOrUndefined(stopPrice);
    const pct = numberOrUndefined(riskPct);
    if (equity == null || stop == null || pct == null || riskEntryRef == null) return null;
    const riskPerShare = Math.abs(riskEntryRef - stop);
    if (riskPerShare <= 0) return null;
    return Math.floor((equity * (pct / 100)) / riskPerShare);
  })();
  const canSubmit = Boolean(order && preview?.can_submit) && !submitting;
  const disabledReason = canSubmit
    ? null
    : (missing ??
      (preview && !preview.can_submit
        ? "Order placement is switched off. Set TRADING_ENABLED=true in backend/.env and restart."
        : null));

  const openConfirm = () => {
    clientOrderIdRef.current = randomUUID();
    setPlaced(null);
    setConfirming(true);
  };

  const doSubmit = async () => {
    if (!order) return;
    setSubmitting(true);
    try {
      const ticket: OrderTicketRequest = {
        symbol: order.symbol,
        side: order.side as "buy" | "sell",
        order_type: order.order_type as EntryOrderType,
        // Carried from the priced order rather than recomputed: the user
        // confirmed a ticket that said gtc or day, and submitting without it
        // would silently fall back to the default -- which is how a bracket
        // ends up as a day order nobody chose.
        time_in_force: order.time_in_force as "day" | "gtc",
        ...(order.limit_price !== null ? { limit_price: order.limit_price } : {}),
        ...(order.stop_price !== null ? { stop_price: order.stop_price } : {}),
        ...(order.take_profit_price !== null ? { take_profit_price: order.take_profit_price } : {}),
        // Submit the resolved quantity rather than re-sending the risk inputs:
        // the user confirmed a specific size, and re-sizing server-side could
        // silently place a different one if the price moved between the
        // preview and the click.
        qty: order.qty,
        ...(order.stop_loss_price !== null ? { stop_loss_price: order.stop_loss_price } : {}),
        client_order_id: clientOrderIdRef.current ?? undefined,
      };
      const result = await submitOrder(ticket);
      setPlaced(result.order?.id ?? "submitted");
      setRejection(null);
      setError(null);
      setConfirming(false);
      onSubmitted();
    } catch (err: unknown) {
      if (err instanceof OrderRejectedError) {
        setRejection(err.detail);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  };

  // What the ticket will actually be sent as: the server's own answer once it
  // has priced one, the same rule applied locally before that.
  const effectiveTimeInForce =
    (preview?.order.time_in_force as "day" | "gtc" | undefined) ??
    timeInForce ??
    (takeProfit.trim() || sizingMode === "risk" ? "gtc" : "day");

  return (
    <div className="order-ticket">
      <div className="order-ticket-row">
        <span className="order-ticket-symbol">{symbol}</span>
        <div className="timeframe-selector side-toggle">
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={side === "buy"}
            onClick={() => setSide("buy")}
            title="Hotkey: B"
          >
            Buy
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={side === "sell"}
            onClick={() => setSide("sell")}
            title="Hotkey: S"
          >
            Sell
          </button>
        </div>
      </div>

      <div className="order-ticket-context">
        <span>
          Buying power <strong>{money(account?.buying_power)}</strong>
        </span>
        <span>
          Equity <strong>{money(account?.equity)}</strong>
        </span>
        {position && (
          <span>
            Holding <strong>{position.qty}</strong> @ {money(position.avg_entry_price)}
          </span>
        )}
      </div>

      {exits && (exits.stopLoss !== null || exits.takeProfit !== null) && !existingExitsDismissed && (
        <div className="order-rejection" role="status">
          {symbol} already has
          {exits.stopLoss !== null ? ` a stop at ${formatPrice(exits.stopLoss)}` : ""}
          {exits.stopLoss !== null && exits.takeProfit !== null ? " and" : ""}
          {exits.takeProfit !== null ? ` a target at ${formatPrice(exits.takeProfit)}` : ""}. A new
          order here does not replace {exits.stopLoss !== null && exits.takeProfit !== null ? "them" : "it"}.{" "}
          <button
            type="button"
            className="row-action"
            onClick={() => setExistingExitsDismissed(true)}
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="order-ticket-row">
        <div className="timeframe-selector">
          {ORDER_TYPES.map(({ type, label, title }, i) => (
            <button
              key={type}
              type="button"
              className="timeframe-button"
              aria-pressed={orderType === type}
              onClick={() => setOrderType(type)}
              title={`${title} Hotkey: ${i + 1}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="timeframe-selector">
          {(["day", "gtc"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className="timeframe-button"
              aria-pressed={effectiveTimeInForce === t}
              onClick={() => setTimeInForce(t)}
              title={
                t === "day"
                  ? "Expires at the close -- including any take-profit and stop-loss legs, which leaves an overnight position unprotected."
                  : "Stays working until filled or cancelled, so the protective legs survive the close."
              }
            >
              {t === "day" ? "Day" : "GTC"}
            </button>
          ))}
        </div>
      </div>

      {(NEEDS_TRIGGER.has(orderType) || NEEDS_LIMIT.has(orderType)) && (
        <div className="order-ticket-row">
          {NEEDS_TRIGGER.has(orderType) && (
            <label title="The order rests until price trades through this, then goes in.">
              Trigger
              <input
                type="number"
                step="0.01"
                value={triggerPrice}
                onChange={(e) => setTriggerPrice(e.target.value)}
              />
              <span className="order-hint">rests until price trades through it</span>
            </label>
          )}
          {NEEDS_LIMIT.has(orderType) && (
            <label
              title={
                orderType === "stop_limit"
                  ? "Once triggered, the most a buy pays / least a sell takes."
                  : "Buy at this or lower; sell at this or higher. Above the market on a buy, it fills immediately."
              }
            >
              Limit
              <input
                type="number"
                step="0.01"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
              />
              <span className="order-hint">
                {orderType === "stop_limit" ? "cap once triggered" : "fills at this price or better"}
              </span>
            </label>
          )}
        </div>
      )}

      <div className="order-ticket-row">
        <div className="timeframe-selector">
          {(["risk", "shares"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className="timeframe-button"
              aria-pressed={sizingMode === m}
              onClick={() => setSizingMode(m)}
            >
              {m === "risk" ? "By risk" : "Shares"}
            </button>
          ))}
        </div>
      </div>

      {sizingMode === "shares" ? (
        <div className="order-ticket-row">
          <label>
            Qty
            <input type="number" step="1" value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
          <div
            className="timeframe-selector"
            title={
              sizingPrice == null
                ? "No reference price yet -- pick an order type/price or wait for a position price."
                : buyingPower == null
                  ? "Buying power unavailable."
                  : undefined
            }
          >
            {[25, 50, 75, 100].map((pct) => (
              <button
                key={pct}
                type="button"
                className="timeframe-button"
                disabled={sizingPrice == null || buyingPower == null}
                onClick={() => {
                  if (sizingPrice == null || buyingPower == null) return;
                  const shares = Math.floor(((buyingPower * pct) / 100) / sizingPrice);
                  setQty(String(Math.max(0, shares)));
                }}
              >
                {pct}%
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="order-ticket-row">
          <label>
            Stop
            <input
              type="number"
              step="0.01"
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
            />
          </label>
          <label>
            Risk %
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="10"
              value={riskPct}
              onChange={(e) => setRiskPct(e.target.value)}
            />
            {estimatedRiskShares !== null && (
              <span className="order-hint" style={{ fontStyle: "italic" }}>
                ≈ {estimatedRiskShares.toLocaleString()} sh
              </span>
            )}
          </label>
        </div>
      )}

      <div className="order-ticket-row">
        <label>
          Target
          <input
            type="number"
            step="0.01"
            value={takeProfit}
            onChange={(e) => setTakeProfit(e.target.value)}
          />
        </label>
      </div>

      {rejection && (
        <div className="order-rejection" role="status">
          {rejection.message}
        </div>
      )}
      {error && <div className="order-rejection">{error}</div>}

      {previewPending && <div className="order-hint">Pricing…</div>}

      {order && (
        <div className="order-preview">
          <strong>
            {order.qty.toLocaleString()} sh · {order.notional.toFixed(2)}
          </strong>
          <span>
            {order.order_class !== "simple" ? `${order.order_class} · ` : ""}
            @ {order.entry_reference.toFixed(2)}
          </span>
          {order.risk_amount !== null && (
            <span>
              risk {order.risk_amount.toFixed(2)}
              {order.risk_pct_of_equity !== null ? ` (${order.risk_pct_of_equity}% of equity)` : ""}
              {order.risk_per_share !== null ? ` · ${order.risk_per_share.toFixed(2)}/sh` : ""}
            </span>
          )}
          {preview?.limits && (
            <span>
              Ceilings: {preview.limits.max_order_qty.toLocaleString()} sh /{" "}
              {preview.limits.max_order_notional.toLocaleString()} notional
            </span>
          )}
        </div>
      )}

      {order?.warnings.map((w) => (
        <div key={w} className="order-warning" role="status">
          {w}
        </div>
      ))}

      {placed && <div className="order-preview">Order submitted.</div>}

      {disabledReason && <div className="order-hint">{disabledReason}</div>}

      <button
        type="button"
        className="generate-button"
        disabled={!canSubmit}
        onClick={openConfirm}
        title={disabledReason ?? "Hotkey: Enter"}
      >
        {submitting ? "Submitting…" : `${side === "buy" ? "Buy" : "Sell"} ${symbol}`}
      </button>

      <Modal open={confirming} title="Confirm order" onClose={() => setConfirming(false)}>
        {order && (
          <div className="order-confirm">
            <p className="order-confirm-line">
              <strong>
                {order.side.toUpperCase()} {order.qty.toLocaleString()} {order.symbol}
              </strong>{" "}
              {order.order_type.replace("_", "-")}
              {order.stop_price !== null ? ` · trigger ${order.stop_price}` : ""}
              {order.limit_price !== null ? ` @ ${order.limit_price}` : ""}
            </p>
            {order.warnings.map((w) => (
              <p key={w} className="order-warning">
                {w}
              </p>
            ))}
            <p className="order-confirm-line">
              Notional {order.notional.toFixed(2)}
              {order.risk_amount !== null ? ` · risk ${order.risk_amount.toFixed(2)}` : ""}
              {order.risk_pct_of_equity !== null ? ` (${order.risk_pct_of_equity}% of equity)` : ""}
            </p>
            {(order.stop_loss_price !== null || order.take_profit_price !== null) && (
              <p className="order-confirm-line">
                {order.stop_loss_price !== null ? `Stop ${order.stop_loss_price}` : ""}
                {order.stop_loss_price !== null && order.take_profit_price !== null ? " · " : ""}
                {order.take_profit_price !== null ? `Target ${order.take_profit_price}` : ""}
              </p>
            )}
            <p className="order-confirm-mode">PAPER — simulated account</p>
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="generate-button"
                disabled={submitting}
                onClick={() => void doSubmit()}
              >
                {submitting ? "Submitting…" : "Place order"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
