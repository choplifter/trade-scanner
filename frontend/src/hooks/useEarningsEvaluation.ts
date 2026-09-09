import { useCallback, useRef, useState } from "react";

import { earningsEvaluate } from "../api/earnings";
import type { EarningsEvaluation } from "../types/earnings";

export interface EarningsEvaluationState {
  /** Every symbol evaluated so far this session, by symbol. Kept rather
   * than replaced so collapsing a row and opening it again is free -- the
   * call behind it takes seconds. */
  bySymbol: Record<string, EarningsEvaluation>;
  pending: string | null;
  errors: Record<string, string>;
  run: (symbol: string) => void;
}

/**
 * One reporter's evaluation, fetched when its row is expanded.
 *
 * Imperative rather than symbol-driven (the shape useOptionsOptimizer
 * uses): the reader decides which row is worth several seconds of chain
 * reads, and nothing should start because a row scrolled into view. Only
 * the latest request may write, and a symbol already answered is not
 * asked again.
 */
export function useEarningsEvaluation(): EarningsEvaluationState {
  const [bySymbol, setBySymbol] = useState<Record<string, EarningsEvaluation>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<string | null>(null);
  const seqRef = useRef(0);

  const run = useCallback(
    (symbol: string) => {
      if (bySymbol[symbol]) return;
      const seq = ++seqRef.current;
      setPending(symbol);
      setErrors((prev) => {
        if (!(symbol in prev)) return prev;
        const next = { ...prev };
        delete next[symbol];
        return next;
      });
      earningsEvaluate(symbol)
        .then((answer) => {
          if (seq !== seqRef.current) return;
          setBySymbol((prev) => ({ ...prev, [symbol]: answer }));
        })
        .catch((err) => {
          if (seq !== seqRef.current) return;
          setErrors((prev) => ({ ...prev, [symbol]: String(err instanceof Error ? err.message : err) }));
        })
        .finally(() => {
          if (seq === seqRef.current) setPending(null);
        });
    },
    [bySymbol],
  );

  return { bySymbol, pending, errors, run };
}
