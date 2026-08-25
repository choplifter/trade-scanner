import { useCallback, useEffect, useRef, useState } from "react";

import { getTrades } from "../api/http";
import type { Trade, TradeBucket, TradeSummary, TradesRange } from "../types/trading";

/** A closed trade does not change; this polls only to pick up the next one
 * closing while the tab is open. Same cadence as the fills view. */
const POLL_MS = 15_000;

export interface TradesState {
  trades: Trade[];
  summary: TradeSummary | null;
  /** Per-day subtotals over the period, oldest first. */
  buckets: TradeBucket[];
  /** Symbols with fills but a position still open -- not trades yet. */
  openSymbols: string[];
  loading: boolean;
  error: string | null;
}

/** Closed round trips within a calendar period, newest first, with what
 * they add up to.
 *
 * Each request also makes the backend re-pair the broker's recent fills
 * and persist any newly closed trip, so the list is complete even after a
 * paper-account reset wipes the broker's own history. The period is
 * applied server-side, in ET, so the summary and buckets match the rows.
 *
 * `enabled` keeps the poll off while the view is closed, as in
 * useOrderHistory.
 */
export function useTrades(enabled: boolean, range: TradesRange): TradesState {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [summary, setSummary] = useState<TradeSummary | null>(null);
  const [buckets, setBuckets] = useState<TradeBucket[]>([]);
  const [openSymbols, setOpenSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await getTrades(range);
      if (cancelledRef.current) return;
      setTrades(result.trades);
      setSummary(result.summary);
      setBuckets(result.buckets);
      setOpenSymbols(result.open_symbols);
      setError(null);
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [range]);

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

  return { trades, summary, buckets, openSymbols, loading, error };
}
