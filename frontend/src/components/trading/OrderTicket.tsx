import { useEffect, useRef, useState } from "react";

import { OrderRejectedError, previewOrder, submitOrder } from "../../api/http";
import { Modal } from "../common/Modal";
import type { OrderPreview, OrderTicketRequest, TradingRejection } from "../../types/trading";

type SizingMode = "shares" | "risk";

/** Debounce: the preview is a round trip that also fetches the account, so
 * firing per keystroke would be wasteful and would make the displayed size
 * lag behind the field being typed into. */
const PREVIEW_DEBOUNCE_MS = 350;

function numberOrUndefined(value: string): number | undefined {
  if (value.trim() === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
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
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [sizingMode, setSizingMode] = useState<SizingMode>("risk");

  const [qty, setQty] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [riskPct, setRiskPct] = useState(String(defaultRiskPct));
  const [takeProfit, setTakeProfit] = useState("");

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
      ...(orderType === "limit" ? { limit_price: numberOrUndefined(limitPrice) } : {}),
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
    const incomplete =
      (sizingMode === "shares" && !ticket.qty) ||
      (sizingMode === "risk" && (!ticket.risk?.stop_price || !ticket.risk?.risk_pct_of_equity)) ||
      (orderType === "limit" && !ticket.limit_price);
    if (incomplete) {
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
  }, [symbol, side, orderType, sizingMode, qty, limitPrice, stopPrice, riskPct, takeProfit]);

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to build an order.</div>;
  }

  const order = preview?.order;
  const canSubmit = Boolean(order && preview?.can_submit) && !submitting;

  const openConfirm = () => {
    clientOrderIdRef.current = crypto.randomUUID();
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
        order_type: order.order_type as "market" | "limit",
        ...(order.limit_price !== null ? { limit_price: order.limit_price } : {}),
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
          {(["market", "limit"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className="timeframe-button"
              aria-pressed={orderType === t}
              onClick={() => setOrderType(t)}
            >
              {t === "market" ? "Market" : "Limit"}
            </button>
          ))}
        </div>
        {orderType === "limit" && (
          <label>
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

      {placed && <div className="order-preview">Order submitted.</div>}

      <button
        type="button"
        className="generate-button"
        disabled={!canSubmit}
        onClick={openConfirm}
        title={
          preview && !preview.can_submit
            ? "Order placement is switched off. Set TRADING_ENABLED=true in backend/.env and restart."
            : undefined
        }
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
              {order.order_type}
              {order.limit_price !== null ? ` @ ${order.limit_price}` : ""}
            </p>
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
