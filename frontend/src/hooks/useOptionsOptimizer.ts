import { useCallback, useRef, useState } from "react";

import { compareHorizons, optimizeStructures } from "../api/options";
import type { HorizonsResponse, OptimizeRequest, OptimizeResponse } from "../types/options";

export interface OptimizerState {
  /** The last answer received; the tab shows it only while it is for the
   * symbol on screen (`result.underlying`). */
  result: OptimizeResponse | null;
  /** What produced `result`, so the form can show it again after a tab
   * switch instead of resetting to defaults. */
  request: OptimizeRequest | null;
  loading: boolean;
  error: string | null;
  run: (body: OptimizeRequest) => void;
  /** The same view across several horizons -- see compareHorizons. Kept
   * beside `result` rather than replacing it: the comparison says which
   * holding period to look at, the single run is what is looked at. */
  comparison: HorizonsResponse | null;
  comparing: boolean;
  compare: (body: OptimizeRequest) => void;
}

/**
 * The Optimizer tab's request, owned by the widget rather than the tab --
 * the same arrangement as useOptionsIdeas, for the same reason: "Load into
 * ticket" switches to the Chain tab, and an answer that took a few seconds
 * of chain loading and a dozen previews should still be there on the way
 * back. Only the latest request may write (a symbol change mid-flight
 * followed by a new run must not have the older answer land on top of it).
 */
export function useOptionsOptimizer(): OptimizerState {
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [request, setRequest] = useState<OptimizeRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<HorizonsResponse | null>(null);
  const [comparing, setComparing] = useState(false);
  const seqRef = useRef(0);
  const compareSeqRef = useRef(0);

  const run = useCallback((body: OptimizeRequest) => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    setRequest(body);
    optimizeStructures(body)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setResult(res);
      })
      .catch((err) => {
        if (seq !== seqRef.current) return;
        setError(String(err instanceof Error ? err.message : err));
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, []);

  const compare = useCallback((body: OptimizeRequest) => {
    const seq = ++compareSeqRef.current;
    setComparing(true);
    setError(null);
    compareHorizons(body)
      .then((res) => {
        if (seq !== compareSeqRef.current) return;
        setComparison(res);
      })
      .catch((err) => {
        if (seq !== compareSeqRef.current) return;
        setError(String(err instanceof Error ? err.message : err));
      })
      .finally(() => {
        if (seq === compareSeqRef.current) setComparing(false);
      });
  }, []);

  return { result, request, loading, error, run, comparison, comparing, compare };
}
