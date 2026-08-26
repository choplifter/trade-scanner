import { useEffect, useRef, useState } from "react";

import { OrderRejectedError, previewOrder, submitOrder } from "../../api/http";
import { Modal } from "../common/Modal";
import type {
  EntryOrderType,
  OrderPreview,
  OrderTicketRequest,
  TradingRejection,
} from "../../types/trading";

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
export function OrderTicket({ symbol, defaultRiskPct, onSubmitted }: OrderTicketProps) {
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
  const [rejection, setRejection] = useState<TradingRejection | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [placed, setPlaced] = useState<string | null>(null);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Minted once when the dialog opens, not per attempt: Alpaca rejects a
  // duplicate client_order_id, so retrying after a timeout resubmits the
  // *same* order rather than opening a second position. A server-generated
  // id would defeat that, since a retry would arrive with a new one.
  const clientOrderIdRef = useRef<string | null>(null);

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
          : !numberOrUndefined(riskPct)
            ? "Enter a risk %"
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
      return;
    }

    if (timerRef.current) clearTimeout(timerRef.current);
    let cancelled = false;
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
          >
            Buy
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={side === "sell"}
            onClick={() => setSide("sell")}
          >
            Sell
          </button>
        </div>
      </div>

      <div className="order-ticket-row">
        <div className="timeframe-selector">
          {ORDER_TYPES.map(({ type, label, title }) => (
            <button
              key={type}
              type="button"
              className="timeframe-button"
              aria-pressed={orderType === type}
              onClick={() => setOrderType(type)}
              title={title}
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
              value={riskPct}
              onChange={(e) => setRiskPct(e.target.value)}
            />
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
        title={disabledReason ?? undefined}
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
