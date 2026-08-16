import { useState } from "react";

import { BacktestRefusedError, backtestScreen } from "../../api/http";
import type { BacktestRefusal, Screen, ScreenBacktestResponse } from "../../types/screener";

interface Props {
  screen: Screen;
  onClose: () => void;
}

const LOOKBACK_OPTIONS = [60, 120, 180, 365];
const HORIZON_OPTIONS = [1, 2, 3, 5];

function pct(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

function signed(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/**
 * "How would this screen have done?" — replays the current filters over
 * historical daily bars.
 *
 * Deliberately reports alpha beside raw win rate. A raw win rate near 50% is
 * what a coin flip looks like, and on a broadly green day every long closes
 * positive; only the benchmark-relative number says whether the *screen*
 * contributed anything.
 */
export function ScreenBacktestPanel({ screen, onClose }: Props) {
  const [lookback, setLookback] = useState(180);
  const [horizon, setHorizon] = useState(1);
  const [result, setResult] = useState<ScreenBacktestResponse | null>(null);
  const [refusal, setRefusal] = useState<BacktestRefusal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const run = () => {
    setLoading(true);
    setError(null);
    setRefusal(null);
    backtestScreen(screen, { lookback_days: lookback, horizon_days: horizon })
      .then((res) => {
        setResult(res);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof BacktestRefusedError) {
          setRefusal(err.detail);
          setResult(null);
        } else {
          setError(err instanceof Error ? err.message : "Backtest failed");
        }
        setLoading(false);
      });
  };

  const alpha = result?.alpha?.[0] ?? null;

  return (
    <div className="screen-backtest">
      <div className="screen-backtest-controls">
        <strong>Backtest this screen</strong>
        <label>
          Lookback
          <select value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
            {LOOKBACK_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {d}d
              </option>
            ))}
          </select>
        </label>
        <label>
          Hold
          <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
            {HORIZON_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {d}d
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={run} disabled={loading}>
          {loading ? "Running…" : "Run"}
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>

      {loading && <p className="screener-summary">Fetching daily bars — this takes a moment.</p>}

      {refusal && (
        <div className="screen-backtest-refusal">
          <strong>{refusal.message}</strong>
          <p>
            Can't replay: {refusal.unsupported_fields.join(", ")}. {refusal.reason}
          </p>
        </div>
      )}

      {error && <p className="widget-error">{error}</p>}

      {result && !refusal && (
        <div className="screen-backtest-result">
          <p className="screener-summary">
            {result.sample_size} picks over {result.lookback_days} days ·{" "}
            {result.symbols_with_bars}/{result.symbol_count} symbols · {result.horizon_days}-day hold
            {result.sample_size < result.min_sample_size && (
              <span className="custom-tab"> · below the n={result.min_sample_size} floor, treat as noise</span>
            )}
          </p>

          {alpha ? (
            <table className="scanner-table">
              <thead>
                <tr>
                  <th>Picks</th>
                  <th>Win rate</th>
                  <th>Beat {result.benchmark_symbol}</th>
                  <th>Avg alpha</th>
                  <th>Median alpha</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{alpha.sample_size}</td>
                  <td>{pct(alpha.win_rate)}</td>
                  <td>{pct(alpha.alpha_win_rate)}</td>
                  <td className={(alpha.avg_alpha ?? 0) >= 0 ? "delta-up" : "delta-down"}>
                    {signed(alpha.avg_alpha)}
                  </td>
                  <td className={(alpha.median_alpha ?? 0) >= 0 ? "delta-up" : "delta-down"}>
                    {signed(alpha.median_alpha)}
                  </td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="screener-summary">No picks matched this screen historically.</p>
          )}

          <p className="screener-summary">
            Win rate counts closing positive; "Beat {result.benchmark_symbol}" counts beating the
            market over the same window — the second is the one that says whether the screen added
            anything. Today's universe is applied to past dates, so results carry survivorship bias.
          </p>
        </div>
      )}
    </div>
  );
}
