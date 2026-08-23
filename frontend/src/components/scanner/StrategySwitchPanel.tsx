import { useEffect, useState } from "react";

import {
  getStrategies,
  setStrategyEnabled,
  type StrategiesResponse,
  type StrategySwitch,
} from "../../api/http";

/**
 * The on/off switch for each strategy signal. Server-side and persistent:
 * flipping one changes what the scanner marks on its next tick, what the
 * chart's signal lines draw, and what a full backtest run includes -- this
 * panel is a remote control, not a display filter.
 *
 * A plain checkbox list above the table, like the screener's column picker,
 * rather than a popover: it pushes the table down and stays keyboard-
 * navigable with no focus trapping to get right.
 */
export function StrategySwitchPanel() {
  const [strategies, setStrategies] = useState<StrategySwitch[]>([]);
  const [loadErrors, setLoadErrors] = useState<StrategiesResponse["errors"]>([]);
  const [error, setError] = useState<string | null>(null);
  // The stem being saved, so a slow request disables just that checkbox
  // instead of letting a second click race the first.
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStrategies()
      .then((res) => {
        if (cancelled) return;
        setStrategies(res.strategies);
        setLoadErrors(res.errors);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load strategies");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (strategy: StrategySwitch) => {
    setPending(strategy.stem);
    setError(null);
    setStrategyEnabled(strategy.stem, !strategy.enabled)
      .then((res) => {
        setStrategies(res.strategies);
        setLoadErrors(res.errors);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to switch strategy");
      })
      .finally(() => setPending(null));
  };

  return (
    <div className="strategy-switches">
      {strategies.map((strategy) => (
        <label key={strategy.stem} title={strategy.filename}>
          <input
            type="checkbox"
            checked={strategy.enabled}
            disabled={pending === strategy.stem}
            onChange={() => toggle(strategy)}
          />
          {strategy.name}
        </label>
      ))}
      {strategies.length === 0 && !error && <span className="strategy-switches-note">Loading…</span>}
      {loadErrors.map((e) => (
        <span key={e.filename} className="strategy-switches-error" title={e.error}>
          {e.filename} failed to load
        </span>
      ))}
      {error && <span className="strategy-switches-error">{error}</span>}
    </div>
  );
}
