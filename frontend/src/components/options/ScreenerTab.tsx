import { useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { screenUnderlyings } from "../../api/options";
import { useWatchlist } from "../../hooks/useWatchlist";
import type { LoadableStructure, ScreenResponse, ScreenRow, ScreenStrategy, Strategy } from "../../types/options";
import { formatPrice } from "../../utils/format";
import { requestTicket } from "./ticketIntent";

interface ScreenerTabProps {
  /** Clicking a row loads that symbol into the widget (and the chart). */
  onSelectSymbol?: (symbol: string) => void;
}

const STRATEGIES: { key: ScreenStrategy; label: string; title: string }[] = [
  {
    key: "cash_secured_put",
    label: "Cash-sec. put",
    title: "Wants implied volatility rich against realised, deep open interest and tight quotes at a 0.10-0.20 delta put.",
  },
  {
    key: "covered_call",
    label: "Covered call",
    title: "The same chain qualities, read off the call side.",
  },
  {
    key: "credit_spread",
    label: "Credit spread",
    title: "As above, and the chain must list a strike further out to buy as the wing -- with enough credit against the width it risks.",
  },
  {
    key: "iron_condor",
    label: "Iron condor",
    title: "A wing on both sides, and both verticals paying enough against their width.",
  },
  {
    key: "debit_spread",
    label: "Debit spread",
    title: "Wants implied volatility cheap against realised, and a wing to sell against the long leg.",
  },
  {
    key: "long_option",
    label: "Long option",
    title: "Cheap implied volatility and quotes tight enough that the debit is not the spread.",
  },
  {
    key: "calendar",
    label: "Calendar",
    title: "Wants the front expiry's implied volatility above the back one's -- the only screen that costs a second chain fetch per symbol.",
  },
];

/**
 * The structure the row's own numbers already describe, ready for the
 * ticket -- the screener found the expiry and the strikes, so sending the
 * reader back to the chain to re-pick them by hand would be busywork.
 *
 * Null where the row does not name a shape: a long option or a debit
 * spread has no short strike to build from (the screen judged the chain,
 * not a direction), and a calendar's second leg is in another expiry the
 * row carries only as a date. Those rows offer the chain instead.
 */
export function structureOf(row: ScreenRow, strategy: ScreenStrategy): LoadableStructure | null {
  if (!row.expiry) return null;
  const base = { underlying: row.symbol, expiry: row.expiry, qty: 1 };
  const put = row.short_put;
  const call = row.short_call;
  if (strategy === "cash_secured_put" && put) {
    return { strategy: "cash_secured_put" as Strategy, ticket: { ...base, strategy: "cash_secured_put" as Strategy, short_strike: put.strike } };
  }
  if (strategy === "covered_call" && call) {
    return { strategy: "covered_call" as Strategy, ticket: { ...base, strategy: "covered_call" as Strategy, short_strike: call.strike } };
  }
  if (strategy === "credit_spread" && row.put_spread) {
    // A put vertical written below the money: bullish, hence bull_put.
    return {
      strategy: "bull_put" as Strategy,
      ticket: {
        ...base,
        strategy: "bull_put" as Strategy,
        long_strike: row.put_spread.long_strike,
        short_strike: row.put_spread.short_strike,
      },
    };
  }
  if (strategy === "iron_condor" && row.put_spread && row.call_spread) {
    return {
      strategy: "iron_condor" as Strategy,
      ticket: {
        ...base,
        strategy: "iron_condor" as Strategy,
        put_long_strike: row.put_spread.long_strike,
        put_short_strike: row.put_spread.short_strike,
        call_short_strike: row.call_spread.short_strike,
        call_long_strike: row.call_spread.long_strike,
      },
    };
  }
  return null;
}

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
  const [strategy, setStrategy] = useState<ScreenStrategy>("cash_secured_put");
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const run = async () => {
    if (symbols.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await screenUnderlyings({ symbols: symbols.slice(0, 60), strategy }));
    } catch (err: unknown) {
      setError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="opt-screener">
      <div className="opt-preference">
        {STRATEGIES.map((b) => (
          <button
            key={b.key}
            type="button"
            className="timeframe-button"
            aria-pressed={strategy === b.key}
            onClick={() => setStrategy(b.key)}
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
                {result.rows.some((r) => r.put_spread || r.call_spread) && (
                  <th scope="col" title="The wing the chain offers, and what comes back as credit per dollar of width.">
                    Vertical
                  </th>
                )}
                {result.rows.some((r) => r.term_ratio != null) && (
                  <th scope="col" title="The front expiry's implied volatility over the back one's.">Front/back</th>
                )}
                <th scope="col" aria-label="Actions" />
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
                    {result.rows.some((r) => r.put_spread || r.call_spread) && (
                      <td>
                        {(() => {
                          const v = row.put_spread ?? row.call_spread;
                          return v
                            ? `${formatPrice(v.long_strike)}/${formatPrice(v.short_strike)} · ${v.width}w · ${pct(v.credit_to_width)}`
                            : "—";
                        })()}
                      </td>
                    )}
                    {result.rows.some((r) => r.term_ratio != null) && (
                      <td>{row.term_ratio == null ? "—" : `${row.term_ratio.toFixed(2)}×`}</td>
                    )}
                    <td className="screen-actions">
                      {(() => {
                        const structure = structureOf(row, result.strategy);
                        return structure ? (
                          <button
                            type="button"
                            className="timeframe-button"
                            title="Load these strikes into the spread ticket on the Chain tab"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectSymbol?.(row.symbol);
                              requestTicket({ symbol: row.symbol, structure });
                            }}
                          >
                            Ticket
                          </button>
                        ) : null;
                      })()}
                    </td>
                  </tr>
                  {open === row.symbol && (
                    <tr key={`${row.symbol}-criteria`} className="screen-detail">
                      <td colSpan={12}>
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
