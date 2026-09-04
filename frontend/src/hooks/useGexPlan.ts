import { useEffect, useState } from "react";

import { getGexPlan } from "../api/http";
import type { GexPlanResponse } from "../types/gex";

// Matches useGexLevels'/useGexReading's REFRESH_MS (backend's
// gex_refresh_interval, 300s) -- there's nothing new to poll for in
// between.
const REFRESH_MS = 5 * 60_000;

/**
 * The /api/meta/gex-plan response for one symbol, asked for by name so the
 * backend computes it if it holds nothing -- any optionable ticker, not the
 * five that used to be precomputed. Independent poller from
 * useGexReading/useGexLevels, same accepted-duplication tradeoff noted
 * there.
 *
 * `loading` covers the first fetch for a symbol only: a cold one costs a
 * real chain fetch on the server, so without it the widget would sit on an
 * empty state that reads as "no data" when it means "not yet".
 */
export function useGexPlan(symbol: string | null): {
  plan: GexPlanResponse | null;
  loading: boolean;
} {
  const [plan, setPlan] = useState<GexPlanResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!symbol) {
      setPlan(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setPlan(null);
    setLoading(true);
    const fetchPlan = (first: boolean) => {
      getGexPlan(symbol)
        .then((res) => {
          if (cancelled) return;
          setPlan(res);
          if (first) setLoading(false);
        })
        .catch(() => {
          if (!cancelled && first) setLoading(false);
        });
    };
    fetchPlan(true);
    const interval = setInterval(() => fetchPlan(false), REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol]);

  return { plan, loading };
}
