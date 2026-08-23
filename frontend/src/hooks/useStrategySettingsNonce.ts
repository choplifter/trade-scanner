import { useEffect, useState } from "react";

/**
 * How the Signals panel tells the chart its ground truth moved. The panel
 * (in the scanner) and the chart are siblings with no shared state, but a
 * server-side signal setting -- the opening-range length, a strategy
 * switched off -- changes what /bars returns: the Opening Range box, the
 * Strategy Signal stop/target lines. A window event keeps the coupling to
 * one string instead of threading state through App.
 *
 * Deliberately fired only after a *successful mutation*, never on the
 * panel's initial load -- the chart already fetched fresh data for itself,
 * and announcing on load would double-fetch every time the panel opens.
 */
const EVENT = "strategy-settings-changed";

export function announceStrategySettingsChange(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** Bumps whenever the settings change -- put it in a fetch effect's deps to
 * refetch on every announcement. */
export function useStrategySettingsNonce(): number {
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const bump = () => setNonce((n) => n + 1);
    window.addEventListener(EVENT, bump);
    return () => window.removeEventListener(EVENT, bump);
  }, []);

  return nonce;
}
