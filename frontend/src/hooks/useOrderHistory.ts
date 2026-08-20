import { useCallback, useEffect, useRef, useState } from "react";

import { getOrders } from "../api/http";
import type { Order } from "../types/trading";

/** Filled orders are history -- they do not change once written, so this
 * polls only slowly, to pick up fills that landed since the tab was opened. */
const POLL_MS = 15_000;

export interface OrderHistoryState {
  fills: Order[];
  loading: boolean;
  error: string | null;
}

/** The account's completed fills, newest first.
 *
 * Alpaca's "closed" query returns everything that is no longer working,
 * which includes cancelled and expired orders -- neither of which is a
 * trade. Filtering to actually-filled here keeps the table an account of
 * what happened rather than of what was attempted; the Working tab is where
 * live orders belong.
 *
 * `enabled` keeps the poll off while the tab is closed, as in
 * useBalanceHistory.
 */
export function useOrderHistory(enabled: boolean): OrderHistoryState {
  const [fills, setFills] = useState<Order[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await getOrders("closed");
      if (cancelledRef.current) return;
      const filled = result.orders.filter(
        (order) => order.status === "filled" && Number(order.filled_qty ?? 0) > 0,
      );
      filled.sort((a, b) => timeOf(b) - timeOf(a));
      setFills(filled);
      setError(null);
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    cancelledRef.current = false;
    setLoading(true);
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [enabled, load]);

  return { fills, loading, error };
}

/** Sort key: when the order actually filled, falling back to when it was
 * sent for the rare fill that arrives with no filled_at. */
function timeOf(order: Order): number {
  const stamp = order.filled_at ?? order.submitted_at ?? order.created_at;
  if (!stamp) return 0;
  const parsed = Date.parse(stamp);
  return Number.isFinite(parsed) ? parsed : 0;
}
