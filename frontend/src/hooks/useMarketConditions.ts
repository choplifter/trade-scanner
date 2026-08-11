import { useEffect, useState } from "react";

import { getMarketConditions } from "../api/http";
import type { MarketConditionsResponse } from "../types/marketConditions";

const UNAVAILABLE: MarketConditionsResponse = { available: false };

export function useMarketConditions(pollMs = 60_000): MarketConditionsResponse {
  const [conditions, setConditions] = useState<MarketConditionsResponse>(UNAVAILABLE);

  useEffect(() => {
    let cancelled = false;

    const fetchConditions = () => {
      getMarketConditions()
        .then((res) => {
          if (!cancelled) setConditions(res);
        })
        .catch(() => {});
    };

    fetchConditions();
    const interval = setInterval(fetchConditions, pollMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [pollMs]);

  return conditions;
}
