import { useEffect, useRef, useState } from "react";

import { OrderRejectedError, previewOrder } from "../../api/http";
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
}

/** The order ticket. Sizes and prices through the server on every edit, so
 * what is shown is what the broker would receive -- the arithmetic is never
 * duplicated client-side, where it could drift from the ceilings that
 * actually gate a submit.
 *
 * Submit is not wired up yet: this milestone deliberately ships the whole
 * validation surface with no write path, so every refusal can be exercised
 * before an order is possible. */
export function OrderTicket({ symbol, defaultRiskPct }: OrderTicketProps) {
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

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

      <button
        type="button"
        className="generate-button"
        disabled
        title={
          preview && !preview.can_submit
            ? "Order placement is switched off. Set TRADING_ENABLED=true in backend/.env."
            : "Order submission is not implemented yet -- preview only."
        }
      >
        Submit (preview only)
      </button>
    </div>
  );
}
