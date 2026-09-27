/**
 * Why the Optimizer's "Find structures" button is disabled.
 *
 * A dead button with nothing beside it reads as a broken panel, and the
 * commonest reason is not the reader's doing at all: a symbol with no
 * listed options can never be optimized, however the panel is filled in.
 * Null when the run can go ahead (or is already running).
 */
export interface BlockedInput {
  symbol: string | null;
  /** Expiries the chain listed; empty while it loads and for a symbol
   * that has no options at all -- `chainLoading` tells the two apart. */
  expiryCount: number;
  chainLoading: boolean;
  horizonExpiry: string;
  familyCount: number;
  /** The target price as a number, null when the field is empty or not a
   * price. */
  target: number | null;
  /** The chain's spot: without it the outlook buttons cannot price a
   * target yet. */
  spot: number | null;
  running: boolean;
}

export function optimizerBlockedReason(input: BlockedInput): string | null {
  const { symbol, expiryCount, chainLoading, horizonExpiry, familyCount, target, spot, running } = input;
  if (running) return null;
  if (!symbol) return "Pick a symbol to optimize a structure for.";
  if (expiryCount === 0) {
    // The fetch's own flag, not the absence of a spot: a symbol with no
    // options never gets a chain, so "no spot yet" would read as "loading"
    // for ever.
    return chainLoading
      ? "Loading the option chain…"
      : `${symbol} has no listed options, so there is nothing to build from.`;
  }
  if (horizonExpiry === "") return "Pick a horizon expiry above.";
  if (familyCount === 0) return "Pick at least one strategy family under More options.";
  if (target == null) {
    return spot == null
      ? "Waiting for the option chain; the outlook buttons set a target as soon as it is in."
      : "Pick an outlook, or type a target price.";
  }
  return null;
}
