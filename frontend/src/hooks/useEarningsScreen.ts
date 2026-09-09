import { useCallback, useEffect, useRef, useState } from "react";

import { earningsFacts, earningsScreen } from "../api/earnings";
import type { EarningsScreenResponse, SymbolFacts } from "../types/earnings";

/** How many of the listed reporters get their chain read. The backend
 * caps a facts call at 30, and past the first screenful the marginal row
 * is one nobody scrolls to. */
const FACTS_LIMIT = 30;
/** The list is a calendar and a batch of daily bars; five minutes is
 * often enough to catch a company publishing mid-session. */
const POLL_MS = 5 * 60 * 1000;

export interface EarningsScreenState {
  screen: EarningsScreenResponse | null;
  facts: Record<string, SymbolFacts>;
  loading: boolean;
  /** True while the chains behind the implied-move column are still
   * being read -- the table is already on screen by then. */
  factsLoading: boolean;
  error: string | null;
  refresh: () => void;
}

/**
 * The Earnings screen's list, and the per-symbol facts that fill in its
 * two most interesting columns.
 *
 * Two requests rather than one because they cost differently: the list
 * answers in milliseconds and the facts take a chain read per symbol, so
 * making the table wait for them would trade the whole screen for two
 * columns. Only the latest request may write -- a refresh landing after a
 * later one must not overwrite it.
 */
export function useEarningsScreen(enabled = true): EarningsScreenState {
  const [screen, setScreen] = useState<EarningsScreenResponse | null>(null);
  const [facts, setFacts] = useState<Record<string, SymbolFacts>>({});
  const [loading, setLoading] = useState(false);
  const [factsLoading, setFactsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const seqRef = useRef(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const seq = ++seqRef.current;

    const load = async () => {
      setLoading(true);
      try {
        const list = await earningsScreen();
        if (cancelled || seq !== seqRef.current) return;
        setScreen(list);
        setError(null);
        const symbols = list.rows.slice(0, FACTS_LIMIT).map((r) => r.symbol);
        if (symbols.length === 0) {
          setFacts({});
          return;
        }
        setFactsLoading(true);
        try {
          const answer = await earningsFacts(symbols);
          if (cancelled || seq !== seqRef.current) return;
          setFacts(answer.facts);
        } catch {
          // The list is the answer; the two extra columns simply stay
          // empty rather than taking the screen down with them.
          if (!cancelled && seq === seqRef.current) setFacts({});
        } finally {
          if (!cancelled && seq === seqRef.current) setFactsLoading(false);
        }
      } catch (err) {
        if (cancelled || seq !== seqRef.current) return;
        setError(String(err instanceof Error ? err.message : err));
      } finally {
        if (!cancelled && seq === seqRef.current) setLoading(false);
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, tick]);

  return { screen, facts, loading, factsLoading, error, refresh };
}
