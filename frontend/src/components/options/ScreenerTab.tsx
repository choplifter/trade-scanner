import { useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { screenUnderlyings } from "../../api/options";
import { useWatchlist } from "../../hooks/useWatchlist";
import type { ScreenBias, ScreenResponse, ScreenRow } from "../../types/options";
import { formatPrice } from "../../utils/format";

interface ScreenerTabProps {
  /** Clicking a row loads that symbol into the widget (and the chart). */
  onSelectSymbol?: (symbol: string) => void;
}

const BIASES: { key: ScreenBias; label: string; title: string }[] = [
  {
    key: "sell_premium",
    label: "Sell premium",
    title: "Cash-secured puts, covered calls, credit spreads, condors: wants implied volatility rich against realised, deep open interest and tight quotes.",
  },
  {
    key: "buy_premium",
    label: "Buy premium",
    title: "Long calls and puts, debit spreads: wants implied volatility cheap against realised, and quotes tight enough that the debit is not the spread.",
  },
  { key: "neutral", label: "Just the numbers", title: "Reports the volatility comparison without judging it." },
];

function pct(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)} %`;
}

function mark(passed: boolean | null): string {
  return passed === null ? "?" : passed ? "✓" : "✗";
}

/** One row's criteria, folded out on click: every measure with what it
 * found, so a "✗" can always be traced to a number. */
function Criteria({ row }: { row: ScreenRow }) {
  return (
    <ul className="screen-criteria">
      {row.criteria.map((c) => (
        <li key={c.key} className={c.passed === null ? "unknown" : c.passed ? "pass" : "fail"}>
          <span className="screen-mark">{mark(c.passed)}</span> <strong>{c.label}:</strong> {c.detail}
        </li>
      ))}
      {row.note && <li className="unknown">{row.note}</li>}
    </ul>
  );
}

/**
 * Which underlyings suit a strategy -- the Optimizer's question one level
 * up (backend app/options/screener.py). The symbols are the watchlist:
 * every row costs a chain fetch, so this screens a list you already keep
 * rather than a universe.
 */
export function ScreenerTab({ onSelectSymbol }: ScreenerTabProps) {
  const { symbols } = useWatchlist();
  const [bias, setBias] = useState<ScreenBias>("sell_premium");
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const run = async () => {
    if (symbols.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await screenUnderlyings({ symbols: symbols.slice(0, 60), bias }));
    } catch (err: unknown) {
      setError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="opt-screener">
      <div className="opt-preference">
        {BIASES.map((b) => (
          <button
            key={b.key}
            type="button"
            className="timeframe-button"
            aria-pressed={bias === b.key}
            onClick={() => setBias(b.key)}
            title={b.title}
          >
            {b.label}
          </button>
        ))}
        <button type="button" className="generate-button opt-run" disabled={loading || symbols.length === 0} onClick={() => void run()}>
          {loading ? "Reading chains…" : `Screen ${Math.min(symbols.length, 60)} symbols`}
        </button>
        <span className="order-hint">from your watchlist · one chain fetch each</span>
      </div>

      {error && <p className="order-rejection">{error}</p>}
      {symbols.length === 0 && <p className="widget-empty">Add symbols to the watchlist to screen them.</p>}

      {result && (
        <>
          <p className="order-hint">
            {result.criteria.dte[0]}–{result.criteria.dte[1]} DTE · OI {result.criteria.min_open_interest.toLocaleString()}+ ·
            quotes under {pct(result.criteria.max_spread_fraction)} · short delta {result.criteria.short_delta[0].toFixed(2)}–
            {result.criteria.short_delta[1].toFixed(2)}
            {result.criteria.avoid_earnings ? " · no earnings inside" : ""}
          </p>
          <table className="opt-table screen-table">
            <thead>
              <tr>
                <th scope="col">Symbol</th>
                <th scope="col">Passed</th>
                <th scope="col" title="At-the-money implied volatility of the screened expiry.">IV</th>
                <th scope="col" title="Close-to-close volatility of the last 20 sessions, annualised.">RV</th>
                <th scope="col" title="Implied over realised. Above 1.2 the premium is rich, below 0.95 it is cheap.">IV/RV</th>
                <th scope="col" title="Where today's IV sits in this symbol's own recorded range. Needs 20 sessions.">IV rank</th>
                <th scope="col" title="Open interest across the fetched strikes of that expiry.">OI</th>
                <th scope="col">Expiry</th>
                <th scope="col" title="The strike nearest the delta band, and what crossing its quote costs.">Short put</th>
                <th scope="col">Short call</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => (
                <>
                  <tr
                    key={row.symbol}
                    className={`screen-row${open === row.symbol ? " open" : ""}`}
                    onClick={() => setOpen(open === row.symbol ? null : row.symbol)}
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="link-button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectSymbol?.(row.symbol);
                        }}
                        title="Load this symbol's chain"
                      >
                        {row.symbol}
                      </button>
                    </th>
                    <td className={row.passed === row.scored ? "delta-up" : undefined}>
                      {row.passed}/{row.scored}
                    </td>
                    <td>{pct(row.atm_iv)}</td>
                    <td>{pct(row.realised_vol)}</td>
                    <td className={row.iv_rv_ratio == null ? undefined : row.iv_rv_ratio >= 1.2 ? "delta-up" : row.iv_rv_ratio <= 0.95 ? "delta-down" : undefined}>
                      {row.iv_rv_ratio == null ? "—" : `${row.iv_rv_ratio.toFixed(2)}×`}
                    </td>
                    <td title={row.iv_rank == null ? `${row.iv_rank_samples} sessions recorded, 20 needed` : undefined}>
                      {row.iv_rank == null ? "—" : `${row.iv_rank.toFixed(0)} %`}
                    </td>
                    <td>{row.open_interest.toLocaleString()}</td>
                    <td>{row.expiry ? `${row.expiry} (${row.dte}d)` : "—"}</td>
                    <td>
                      {row.short_put
                        ? `${formatPrice(row.short_put.strike)} · Δ${row.short_put.delta.toFixed(2)} · ${pct(row.short_put.spread_fraction)}`
                        : "—"}
                    </td>
                    <td>
                      {row.short_call
                        ? `${formatPrice(row.short_call.strike)} · Δ${row.short_call.delta.toFixed(2)} · ${pct(row.short_call.spread_fraction)}`
                        : "—"}
                    </td>
                  </tr>
                  {open === row.symbol && (
                    <tr key={`${row.symbol}-criteria`} className="screen-detail">
                      <td colSpan={10}>
                        <Criteria row={row} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
          <p className="order-hint">{result.disclaimer}</p>
        </>
      )}
    </div>
  );
}
