import { useCallback, useEffect, useState } from "react";

import { cancelOptionOrder, getOptionOrders } from "../../api/options";
import { useReplaySession } from "../../hooks/useReplaySession";
import type { Order } from "../../types/trading";
import { formatLeg } from "../../utils/occ";

const POLL_MS = 4_000;

function describe(order: Order): string {
  const legs = order.legs && order.legs.length > 0 ? order.legs : null;
  const what = legs
    ? legs.map((leg) => `${leg.side === "buy" ? "+" : "−"}${formatLeg(leg.symbol ?? "")}`).join(" ")
    : formatLeg(order.symbol ?? "");
  const price = order.limit_price != null ? `@ ${Number(order.limit_price).toFixed(2)}` : "market";
  return `${order.side === "buy" ? "Pay" : "Receive"} ${price} · ${order.qty ?? "?"} × ${what}`;
}

/** The simulated book's resting packages (a limit the market has not
 * reached), with a cancel each. Polled like the spreads; refetched on
 * every replay tick. Nothing to show means nothing rendered. */
export function OptionOrders({ onChanged }: { onChanged?: () => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [error, setError] = useState<string | null>(null);
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
  }, [load, replayAsOf]);

  if (orders.length === 0 && !error) return null;

  return (
    <div className="option-orders">
      <span className="option-orders-title">Working packages</span>
      {error && <p className="order-rejection">{error}</p>}
      <ul className="spread-legs">
        {orders.map((order) => (
          <li key={order.id}>
            {describe(order)}{" "}
            <button
              type="button"
              className="row-action"
              onClick={() => {
                cancelOptionOrder(order.id)
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
    </div>
  );
}
