import { useCallback, useRef, useState } from "react";

import { suggestOptionsIdeas } from "../api/options";
import type { OptionsIdeaResponse } from "../types/options";

export interface OptionsIdeasState {
  /** The last answer received; the tab shows it only while it is for the
   * symbol on screen (`result.underlying`). */
  result: OptionsIdeaResponse | null;
  loading: boolean;
  error: string | null;
  generate: (symbol: string) => void;
}

/**
 * The Idea tab's request, owned by the widget rather than the tab. One
 * suggestion takes minutes -- three expiries of chain, the context around
 * them, then the model over all of it -- and "Load into ticket" switches
 * to the Chain tab, which unmounted the tab and with it the answer. The
 * second structure of three was then a second wait. Held here it survives
 * the tab switch, and a request still in flight lands wherever the user
 * has gone in the meantime.
 */
export function useOptionsIdeas(): OptionsIdeasState {
  const [result, setResult] = useState<OptionsIdeaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Only the latest request may write: a symbol change mid-flight followed
  // by a new request must not have the older answer arrive on top of it.
  const seqRef = useRef(0);

  const generate = useCallback((symbol: string) => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    suggestOptionsIdeas(symbol)
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

  return { result, loading, error, generate };
}
