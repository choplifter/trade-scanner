import { useState } from "react";

import { BacktestRefusedError, backtestScreen } from "../../api/http";
import type {
  BacktestPick,
  BacktestRefusal,
  BacktestResolution,
  ChartFocus,
  Screen,
  ScreenBacktestResponse,
} from "../../types/screener";

interface Props {
  screen: Screen;
  onClose: () => void;
  /** Clicking a pick loads that symbol's chart at the entry time. */
  onSelectPick: (focus: ChartFocus) => void;
}

/** Entry times are ISO. An intraday pick has a real time of day; a daily one
 * only knows its session, so it anchors at midnight UTC of that date — enough
 * for the chart's nearest-bar search on a daily series. */
function pickTime(pick: BacktestPick): number {
  const iso = pick.timestamp ?? `${pick.trading_date}T00:00:00Z`;
  return Math.floor(new Date(iso).getTime() / 1000);
}

/** Local time, matching the chart — CandleChart formats its axis and
 * crosshair with Intl.DateTimeFormat(undefined, …), so a pick shown in any
 * other zone would disagree with the chart it loads. */
const LOCAL_DATETIME = new Intl.DateTimeFormat(undefined, {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** The viewer's zone, e.g. "GMT+2" — shown once beside the table rather than
 * on every row, so the times aren't ambiguous the way they were when a local
 * time sat next to an ET date with neither labelled. */
const LOCAL_ZONE =
  new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
    .formatToParts(new Date())
    .find((part) => part.type === "timeZoneName")?.value ?? "local";

/**
 * Entry label.
 *
 * Both halves come from the timestamp so the whole label is one timezone.
 * Deriving the date from `trading_date` instead would reintroduce the
 * original bug in reverse: that field is the ET *session*, so pairing it
 * with a local clock time mixes two zones in one string — and for a viewer
 * far enough east or west, the local date genuinely differs from the
 * session's.
 *
 * A daily pick has no timestamp, only a session, which is inherently an ET
 * concept and has no meaningful local time of day — so it stays as-is rather
 * than being converted from a fictitious midnight.
 */
function pickLabel(pick: BacktestPick): string {
  if (!pick.timestamp) return pick.trading_date;
  return LOCAL_DATETIME.format(new Date(pick.timestamp)).replace(",", "");
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
 * history at the chosen resolution: one bar per session (daily), or every
 * 5 minutes, which is what makes the 1h volume-rate fields replayable.
 *
 * Deliberately reports alpha beside raw win rate. A raw win rate near 50% is
 * what a coin flip looks like, and on a broadly green day every long closes
 * positive; only the benchmark-relative number says whether the *screen*
 * contributed anything.
 */
export function ScreenBacktestPanel({ screen, onClose, onSelectPick }: Props) {
  const [lookback, setLookback] = useState(180);
  const [horizon, setHorizon] = useState(1);
  const [resolution, setResolution] = useState<BacktestResolution>("daily");
  const [result, setResult] = useState<ScreenBacktestResponse | null>(null);
  const [refusal, setRefusal] = useState<BacktestRefusal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const run = (useResolution: BacktestResolution = resolution) => {
    setLoading(true);
    setError(null);
    setRefusal(null);
    backtestScreen(screen, {
      lookback_days: useResolution === "intraday" ? Math.min(lookback, 45) : lookback,
      horizon_days: horizon,
      resolution: useResolution,
    })
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
          Resolution
          <select
            value={resolution}
            onChange={(e) => setResolution(e.target.value as BacktestResolution)}
            title="Daily is fast and covers months. Intraday rebuilds every 5 minutes, which is what makes the 1h volume fields replayable — much heavier, so shorter lookbacks."
          >
            <option value="daily">Daily</option>
            <option value="intraday">Intraday (5m)</option>
          </select>
        </label>
        {resolution === "daily" && (
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
        )}
        <button type="button" onClick={() => run()} disabled={loading}>
          {loading ? "Running…" : "Run"}
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>

      {loading && (
        // Resolution-specific on purpose: this said "daily bars" for both,
        // which is plainly wrong mid-way through a 5-minute replay. The
        // intraday run does also pull daily bars, for the 20-day volume
        // baseline and previous closes, so say that rather than imply it's
        // only 5-minute data.
        <p className="screener-summary">
          {resolution === "intraday"
            ? "Fetching 5-minute bars (plus daily bars for the 20-day volume baseline) — the first run over a set of symbols is slow, repeats are served from the disk cache."
            : "Fetching daily bars — this takes a moment."}
        </p>
      )}

      {refusal && (
        <div className="screen-backtest-refusal">
          <strong>{refusal.message}</strong>
          <p>
            Can't replay: {refusal.unsupported_fields.join(", ")}. {refusal.reason}
          </p>
          {refusal.retry_with_intraday.length > 0 && (
            // The fix is a resolution switch, not deleting the filter the
            // screen was built around -- so offer the switch directly.
            <button
              type="button"
              onClick={() => {
                setResolution("intraday");
                run("intraday");
              }}
            >
              Retry at intraday resolution
            </button>
          )}
        </div>
      )}

      {error && <p className="widget-error">{error}</p>}

      {result && !refusal && (
        <div className="screen-backtest-result">
          <p className="screener-summary">
            {result.sample_size} picks over {result.lookback_days} days ·{" "}
            {result.symbols_with_bars}/{result.symbol_count} symbols ·{" "}
            {result.resolution === "intraday" ? "held to session close" : `${result.horizon_days}-day hold`}
            {result.sample_size < result.min_sample_size && (
              <span className="custom-tab"> · below the n={result.min_sample_size} floor, treat as noise</span>
            )}
          </p>

          {result.replication && result.replication.picks_per_event !== null && (
            // Every qualifying 5-minute bar is a pick, so one surge can
            // contribute a dozen near-identical rows. Stated plainly rather
            // than letting a big sample size be read at face value.
            <p className="screener-summary">
              {result.replication.sample_size} picks come from{" "}
              {result.replication.distinct_symbol_days} distinct symbol-days (
              {result.replication.picks_per_event}× per event) — consecutive bars during one
              surge are highly correlated, so the effective sample is nearer the second number.
            </p>
          )}

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

          {result.picks.length > 0 && (
            <div className="screen-backtest-picks">
              <p className="screener-summary">
                {result.picks_truncated
                  ? `${result.picks.length} picks sampled evenly across the whole period (every statistic above used all ${result.sample_size}).`
                  : `${result.picks.length} picks.`}{" "}
                {result.resolution === "intraday"
                  ? `Newest first, times in your local timezone (${LOCAL_ZONE}) to match the chart.`
                  : "Newest first, dated by trading session."}{" "}
                Click one to load its chart at the entry.
              </p>
              <table className="scanner-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Entry</th>
                    <th>Gap %</th>
                    <th>RVol</th>
                    {result.resolution === "intraday" && <th>RVol 1h</th>}
                    <th>To close</th>
                    <th>Alpha</th>
                  </tr>
                </thead>
                <tbody>
                  {result.picks.map((pick) => (
                    <tr
                      key={`${pick.symbol}|${pick.timestamp ?? pick.trading_date}`}
                      onClick={() =>
                        onSelectPick({
                          symbol: pick.symbol,
                          time: pickTime(pick),
                          // Intraday entries need an intraday chart to be
                          // visible at all; a daily pick would be a sliver.
                          timeframeKey: pick.timestamp ? "5m" : "1D",
                        })
                      }
                      title={`Load ${pick.symbol} at ${pickLabel(pick)}`}
                    >
                      <td>{pick.symbol}</td>
                      <td>{pickLabel(pick)}</td>
                      <td>{signed(pick.entry_pct_change)}</td>
                      <td>{pick.entry_rvol.toFixed(2)}x</td>
                      {result.resolution === "intraday" && (
                        <td>{pick.entry_rvol_1h == null ? "—" : `${pick.entry_rvol_1h.toFixed(2)}x`}</td>
                      )}
                      <td className={pick.pct_change_since_entry >= 0 ? "delta-up" : "delta-down"}>
                        {signed(pick.pct_change_since_entry)}
                      </td>
                      <td className={(pick.alpha_vs_benchmark ?? 0) >= 0 ? "delta-up" : "delta-down"}>
                        {signed(pick.alpha_vs_benchmark)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
