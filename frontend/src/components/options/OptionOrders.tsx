import { useCallback, useEffect, useState } from "react";

import { cancelOptionOrder, getOptionOrders } from "../../api/options";
import { liveConfirmed, type TradingMode } from "../../api/tradingMode";
import { useReplaySession } from "../../hooks/useReplaySession";
import type { Order } from "../../types/trading";
import { formatLeg } from "../../utils/occ";
import { LiveConfirmField } from "../trading/LiveConfirmField";

const POLL_MS = 4_000;

function describe(order: Order): string {
  const legs = order.legs && order.legs.length > 0 ? order.legs : null;
  const what = legs
    ? legs.map((leg) => `${leg.side === "buy" ? "+" : "−"}${formatLeg(leg.symbol ?? "")}`).join(" ")
    : formatLeg(order.symbol ?? "");
  const price = order.limit_price != null ? `@ ${Number(order.limit_price).toFixed(2)}` : "market";
  return `${order.side === "buy" ? "Pay" : "Receive"} ${price} · ${order.qty ?? "?"} × ${what}`;
}

/** The account's resting option orders (a limit the market has not
 * reached): the simulated book's packages, or Alpaca's option orders in
 * Paper and Live -- so a put sold a minute ago that has not filled is
 * found here, next to the spreads, rather than only in the trading
 * widget's Orders tab. A cancel each; in Live the typed confirmation
 * first. Polled like the spreads; refetched on every replay tick.
 * Nothing to show means nothing rendered. */
export function OptionOrders({ mode, onChanged }: { mode: TradingMode; onChanged?: () => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [liveTyped, setLiveTyped] = useState("");
  const replayAsOf = useReplaySession()?.as_of ?? null;

  const load = useCallback(() => {
    getOptionOrders("open")
      .then((res) => {
        setOrders(res.orders);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load, replayAsOf, mode]);

  if (orders.length === 0 && !error) return null;

  return (
    <div className="option-orders">
      <span className="option-orders-title" title="Option orders the market has not filled yet. A fill moves the contract into the spreads below.">
        Working packages
      </span>
      {error && <p className="order-rejection">{error}</p>}
      <ul className="spread-legs">
        {orders.map((order) => (
          <li key={order.id}>
            {describe(order)}
            {order.status && order.status !== "new" && order.status !== "accepted" ? <span className="order-hint"> · {order.status}</span> : null}{" "}
            <button
              type="button"
              className={`row-action${mode === "live" ? " live-action" : ""}`}
              disabled={!liveConfirmed(mode, liveTyped)}
              title={mode === "live" && !liveConfirmed(mode, liveTyped) ? "Type LIVE below to cancel on the real account" : undefined}
              onClick={() => {
                cancelOptionOrder(order.id, mode === "live" ? liveTyped.trim() : undefined)
                  .then(() => {
                    load();
                    onChanged?.();
                  })
                  .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
              }}
            >
              Cancel
            </button>
          </li>
        ))}
      </ul>
      <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
    </div>
  );
}
