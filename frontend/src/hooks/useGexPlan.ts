import { useEffect, useState } from "react";

import { getGexPlan } from "../api/http";
import type { GexPlanResponse } from "../types/gex";

// Matches useGexLevels'/useGexReading's REFRESH_MS (backend's
// gex_refresh_interval, 300s) -- there's nothing new to poll for in
// between.
const REFRESH_MS = 5 * 60_000;

/**
 * The full /api/meta/gex-plan response (every symbol the backend has a
 * reading for -- see app.market_data.gamma_exposure.SYMBOLS), for
 * GexPlanWidget. Independent poller from useGexReading/useGexLevels, same
 * accepted-duplication tradeoff noted there.
 */
export function useGexPlan(): GexPlanResponse | null {
  const [plan, setPlan] = useState<GexPlanResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchPlan = () => {
      getGexPlan()
        .then((res) => {
          if (!cancelled) setPlan(res);
        })
        .catch(() => {});
    };
    fetchPlan();
    const interval = setInterval(fetchPlan, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return plan;
}
