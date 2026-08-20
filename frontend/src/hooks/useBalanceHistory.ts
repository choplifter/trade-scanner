import { useCallback, useEffect, useRef, useState } from "react";

import { getPortfolioHistory } from "../api/http";
import type { BalanceRange, PortfolioHistoryResponse } from "../types/trading";

/** Slower than useTrading's account poll: the curve is a shape read over
 * minutes, not a fill you are waiting on, and each request is a round trip
 * to the broker that returns up to a couple of thousand points. */
const POLL_MS = 30_000;

export interface BalanceHistoryState {
  history: PortfolioHistoryResponse | null;
  loading: boolean;
  error: string | null;
}

/**
 * The account equity curve for one range.
 *
 * Kept out of useTrading because it is range-dependent and only one tab ever
 * wants it -- folding it in would put a broker round trip behind every
 * four-second account poll, for a chart nobody is looking at. `enabled` is
 * what enforces that: the poll does not run while the Balance tab is closed.
 */
export function useBalanceHistory(range: BalanceRange, enabled: boolean): BalanceHistoryState {
  const [history, setHistory] = useState<PortfolioHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);
  // A slow response for the range the user just navigated away from must not
  // overwrite the one they are now looking at.
  const rangeRef = useRef(range);
  rangeRef.current = range;

  const load = useCallback(async (target: BalanceRange) => {
    try {
      const result = await getPortfolioHistory(target);
      if (cancelledRef.current || rangeRef.current !== target) return;
      setHistory(result);
      setError(null);
    } catch (err: unknown) {
      if (cancelledRef.current || rangeRef.current !== target) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!cancelledRef.current && rangeRef.current === target) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    cancelledRef.current = false;
    // Blank the previous range's curve rather than leaving it on screen
    // under the new range's label, which would misattribute it.
    setHistory(null);
    setLoading(true);
    void load(range);
    const timer = setInterval(() => void load(range), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [range, enabled, load]);

  return { history, loading, error };
}
