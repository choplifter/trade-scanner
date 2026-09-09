/**
 * A request from elsewhere on the dashboard -- a scanner row's "Options"
 * button -- for the Options widget to open its Optimizer on a symbol with a
 * view already chosen. A module-level bus rather than dock plumbing: the
 * scanner and the widget are siblings under the dock and share no parent
 * that would want to know about option outlooks.
 *
 * Delivered to widgets mounted at the time of the click only: a widget
 * that mounts later must not jump to its Optimizer for a click it never
 * saw. A consumer marks an intent handled by its `seq`.
 */

import type { OptimizeRequest, OptimizerOutlook } from "../../types/options";

export interface OptimizerIntent {
  symbol: string;
  outlook: OptimizerOutlook;
  /** Why the view was chosen, shown above the results ("gap +12.4 %"). */
  reason?: string;
  /** Fields to override on the request the outlook would have produced --
   * the Earnings screen sends the families and the horizon it scored, so
   * the tab opens on the same question it was asked. Whatever is left out
   * keeps the outlook's own answer. */
  request?: Partial<OptimizeRequest>;
  seq: number;
}

type Listener = (intent: OptimizerIntent) => void;

const listeners = new Set<Listener>();
let seq = 0;

export function requestOptimizer(intent: Omit<OptimizerIntent, "seq">): void {
  const full = { ...intent, seq: ++seq };
  for (const l of listeners) l(full);
}

export function subscribeOptimizerIntent(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The view a scanner move suggests: up moves bullish, down moves bearish,
 * "very" beyond ten percent -- the size the Optimizer's own implied-move
 * arithmetic then turns into a target. */
export function outlookForMove(pctChange: number): OptimizerOutlook {
  if (pctChange >= 10) return "very_bullish";
  if (pctChange >= 0) return "bullish";
  if (pctChange <= -10) return "very_bearish";
  return "bearish";
}
