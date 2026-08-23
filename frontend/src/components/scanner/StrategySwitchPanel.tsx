import { useEffect, useState } from "react";

import {
  getStrategies,
  setMeasuredMoveTarget,
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
  const [measuredMove, setMeasuredMove] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The key being saved (a stem, or "measured-move"), so a slow request
  // disables just that checkbox instead of letting a second click race the
  // first.
  const [pending, setPending] = useState<string | null>(null);

  const apply = (res: StrategiesResponse) => {
    setStrategies(res.strategies);
    setLoadErrors(res.errors);
    setMeasuredMove(res.measured_move_target);
  };

  useEffect(() => {
    let cancelled = false;
    getStrategies()
      .then((res) => {
        if (!cancelled) apply(res);
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
      .then(apply)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to switch strategy");
      })
      .finally(() => setPending(null));
  };

  const toggleMeasuredMove = () => {
    if (measuredMove === null) return;
    setPending("measured-move");
    setError(null);
    setMeasuredMoveTarget(!measuredMove)
      .then(apply)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to switch measured-move fallback");
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
      {measuredMove !== null && (
        <label
          className="strategy-switches-setting"
          title="With no level ahead of an entry, the break rules (ORB, ORB Break, Premarket Range, VWAP Open Range Break) aim at a constructed 2R target instead of declining the trade. Off restores 'no level ahead means no trade'. A level that is ahead but too near still refuses the setup either way."
        >
          <input
            type="checkbox"
            checked={measuredMove}
            disabled={pending === "measured-move"}
            onChange={toggleMeasuredMove}
          />
          Measured-move fallback
        </label>
      )}
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
