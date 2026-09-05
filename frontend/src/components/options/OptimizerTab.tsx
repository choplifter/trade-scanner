import { useEffect, useState } from "react";

import type { OptimizerState } from "../../hooks/useOptionsOptimizer";
import { STRATEGY_GROUPS, type ExpiryInfo, type LoadableStructure, type OptimizerResult, type Strategy } from "../../types/options";
import { formatMoney, formatPrice } from "../../utils/format";
import { formatExpiry, weekdayOf } from "../../utils/occ";

interface OptimizerTabProps {
  symbol: string | null;
  /** The chain's spot, to prefill the target. */
  spot: number | null;
  expiries: ExpiryInfo[];
  optimizer: OptimizerState;
  /** Applies a result's ticket to the widget's own strategy/expiry/legs.
   * Returns false when it could not be loaded, so the card can say so. */
  onLoad: (structure: LoadableStructure) => boolean;
}

/** Diagonals are not enumerated (see backend optimizer.py); income shapes
 * need shares or cash the optimizer cannot see, so they start unticked. */
const NOT_OFFERED = new Set<Strategy>(["diagonal"]);
const DEFAULT_OFF = new Set<Strategy>(["covered_call", "cash_secured_put"]);

function Economics({ r, range }: { r: OptimizerResult; range: boolean }) {
  const { spread } = r;
  const ror = r.return_on_risk;
  return (
    <div className="idea-economics">
      <span title="P/L if the underlying is at the target on the horizon date, each leg's implied volatility unchanged. With a range: the worst point first, then the average and the best.">
        At target{" "}
        <strong className={r.pnl_min > 0 ? "delta-up" : "delta-down"}>
          {range ? `${formatMoney(r.pnl_min)} … ${formatMoney(r.pnl_max)}` : formatMoney(r.pnl_at_target)}
        </strong>
        {range ? <span className="order-hint"> (avg {formatMoney(r.pnl_mean)})</span> : null}
      </span>
      <span title="P/L at the worst point of the target divided by what the account puts up -- the debit paid, or a credit structure's collateral. Not a probability.">
        Return on risk <strong className="optimizer-ror">×{ror.toFixed(2)}</strong>
      </span>
      <span>
        {r.direction === "debit" ? "Pay" : "Receive"} <strong>{formatMoney(Math.abs(r.net_price) * 100 * spread.qty)}</strong>
      </span>
      <span>
        Max profit <strong>{r.max_profit == null ? "unlimited" : formatMoney(r.max_profit)}</strong>
      </span>
      <span>
        Max loss <strong>{r.max_loss == null ? "—" : formatMoney(r.max_loss)}</strong>
      </span>
      {r.breakevens.length > 0 && (
        <span>
          Breakeven <strong>{r.breakevens.map((b) => formatPrice(b)).join(" / ")}</strong>
        </span>
      )}
      <span>
        Collateral <strong>{formatMoney(spread.collateral)}</strong>
      </span>
      <span>{spread.dte}d</span>
    </div>
  );
}

function ResultCard({ r, range, onLoad }: { r: OptimizerResult; range: boolean; onLoad: (s: LoadableStructure) => boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <li className="idea-card">
      <div className="idea-card-header">
        <span className="idea-strategy">
          <span className="optimizer-rank">#{r.rank}</span> {r.strategy_label}
        </span>
        <button type="button" className="timeframe-button" onClick={() => setFailed(!onLoad({ strategy: r.strategy, ticket: r.ticket }))}>
          Load into ticket
        </button>
      </div>
      <div className="idea-legs">
        {r.legs_label} · {weekdayOf(r.expiry)} {formatExpiry(r.expiry)}
      </div>
      <Economics r={r} range={range} />
      {r.spread.warnings.map((warning) => (
        <p key={warning} className="idea-warning">
          {warning}
        </p>
      ))}
      {failed && <p className="order-rejection">Could not load this structure into the ticket.</p>}
    </li>
  );
}

function skippedLine(skipped: { total: number; scored: number; reasons: Record<string, number> }): string {
  const names: Record<string, string> = {
    over_budget: "over budget",
    over_max_loss: "over max loss",
    non_positive_return: "lose at the target",
    no_market: "no market",
    no_iv: "no IV",
    wrong_way_market: "quoted the wrong way",
    risk_shape: "mispriced shape",
    candidate_cap: "beyond the candidate cap",
    strategy_cap: "duplicates of a better one",
  };
  const parts = Object.entries(skipped.reasons)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${n} ${names[k] ?? k}`);
  return `${skipped.total} candidates · ${skipped.scored} priced${parts.length ? " · " + parts.join(" · ") : ""}`;
}

/**
 * OptionStrat's optimizer, on this app's own pipeline: the backend
 * enumerates structures from the listed chain, prices them through the
 * ticket's path and ranks them by return on risk at the target (see backend
 * app/options/optimize.py). Nothing here is a probability or a
 * recommendation -- the cards say what each shape pays if the target is
 * reached, and what it costs.
 */
export function OptimizerTab({ symbol, spot, expiries, optimizer, onLoad }: OptimizerTabProps) {
  const { result, request, loading, error } = optimizer;
  const remembered = request && request.underlying === symbol ? request : null;

  const [low, setLow] = useState<string>(remembered ? String(remembered.target_low) : "");
  const [high, setHigh] = useState<string>(remembered?.target_high != null ? String(remembered.target_high) : "");
  const [range, setRange] = useState(remembered?.target_high != null);
  const [horizonMode, setHorizonMode] = useState<"expiry" | "date">(remembered?.horizon_date ? "date" : "expiry");
  const [horizonExpiry, setHorizonExpiry] = useState<string>(remembered?.horizon_expiry ?? "");
  const [horizonDate, setHorizonDate] = useState<string>(remembered?.horizon_date ?? "");
  const [budget, setBudget] = useState<string>(remembered?.budget != null ? String(remembered.budget) : "");
  const [maxLoss, setMaxLoss] = useState<string>(remembered?.max_loss != null ? String(remembered.max_loss) : "");
  const [families, setFamilies] = useState<Set<Strategy>>(
    () =>
      new Set<Strategy>(
        remembered?.strategies ??
          STRATEGY_GROUPS.flatMap((g) => g.strategies).filter((s) => !NOT_OFFERED.has(s) && !DEFAULT_OFF.has(s)),
      ),
  );

  // A fresh symbol: the target starts at its spot, the horizon at the first
  // expiry with a day left (a contract expiring today has no IV to price
  // a horizon on).
  useEffect(() => {
    if (remembered) return;
    if (spot != null && low === "") setLow(spot.toFixed(2));
    if (horizonExpiry === "") {
      const first = expiries.find((e) => e.dte >= 1);
      if (first) setHorizonExpiry(first.expiry);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, spot, expiries.length]);

  const toggle = (s: Strategy) =>
    setFamilies((cur) => {
      const next = new Set(cur);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  const numeric = (v: string): number | null => {
    const n = Number(v.replace(",", "."));
    return v.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
  };

  const canRun =
    !!symbol &&
    numeric(low) != null &&
    (!range || (numeric(high) != null && (numeric(high) as number) >= (numeric(low) as number))) &&
    (horizonMode === "expiry" ? horizonExpiry !== "" : horizonDate !== "") &&
    families.size > 0;

  const run = () => {
    if (!symbol || !canRun) return;
    optimizer.run({
      underlying: symbol,
      target_low: numeric(low) as number,
      target_high: range ? (numeric(high) as number) : null,
      horizon_expiry: horizonMode === "expiry" ? horizonExpiry : null,
      horizon_date: horizonMode === "date" ? horizonDate : null,
      budget: numeric(budget),
      max_loss: numeric(maxLoss),
      strategies: [...families],
      top_n: 8,
    });
  };

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to optimize a structure for a price target.</div>;
  }
  const shown = result && result.underlying === symbol ? result : null;
  const shownRange = !!shown && shown.target.high > shown.target.low;

  return (
    <div className="idea-tab">
      <div className="optimizer-form">
        <label>
          Target
          <input type="number" step={0.01} min={0.01} value={low} onChange={(e) => setLow(e.target.value)} />
        </label>
        <label className="optimizer-check">
          <input type="checkbox" checked={range} onChange={(e) => setRange(e.target.checked)} /> range
        </label>
        {range && (
          <label>
            to
            <input type="number" step={0.01} min={0.01} value={high} onChange={(e) => setHigh(e.target.value)} />
          </label>
        )}
        <label>
          Horizon
          <span className="timeframe-selector">
            <button type="button" className="timeframe-button" aria-pressed={horizonMode === "expiry"} onClick={() => setHorizonMode("expiry")}>
              Expiry
            </button>
            <button type="button" className="timeframe-button" aria-pressed={horizonMode === "date"} onClick={() => setHorizonMode("date")}>
              Date
            </button>
          </span>
          {horizonMode === "expiry" ? (
            <select value={horizonExpiry} onChange={(e) => setHorizonExpiry(e.target.value)}>
              {expiries
                .filter((e) => e.dte >= 1)
                .map((e) => (
                  <option key={e.expiry} value={e.expiry}>
                    {weekdayOf(e.expiry)} {formatExpiry(e.expiry)} · {e.dte}d
                  </option>
                ))}
            </select>
          ) : (
            <input type="date" value={horizonDate} onChange={(e) => setHorizonDate(e.target.value)} />
          )}
        </label>
        <label title="The most the account puts up per position: the debit paid, or a credit structure's collateral.">
          Budget $
          <input type="number" step={10} min={1} value={budget} placeholder="any" onChange={(e) => setBudget(e.target.value)} />
        </label>
        <label title="The largest defined loss to accept; shapes with an unbounded loss never pass it.">
          Max loss $
          <input type="number" step={10} min={1} value={maxLoss} placeholder="any" onChange={(e) => setMaxLoss(e.target.value)} />
        </label>
        <button type="button" className="generate-button" disabled={!canRun || loading} onClick={run}>
          {loading ? "Pricing structures…" : `Find structures for ${symbol}`}
        </button>
      </div>
      <div className="optimizer-families">
        {STRATEGY_GROUPS.map((group) => (
          <span key={group.label} className="optimizer-family">
            <span className="order-hint">{group.label}</span>
            {group.strategies
              .filter((s) => !NOT_OFFERED.has(s))
              .map((s) => (
                <label key={s} className="optimizer-check">
                  <input type="checkbox" checked={families.has(s)} onChange={() => toggle(s)} />
                  {labelFor(s)}
                </label>
              ))}
          </span>
        ))}
      </div>

      {error && <p className="order-rejection">{error}</p>}
      {loading && (
        <p className="widget-empty">Loading the chain across the horizon's expiries, pricing every candidate, previewing the finalists…</p>
      )}

      {!loading && shown && (
        <>
          <p className="idea-context">
            Target {formatPrice(shown.target.low)}
            {shownRange ? ` – ${formatPrice(shown.target.high)}` : ""} on {weekdayOf(shown.horizon.date)}{" "}
            {formatExpiry(shown.horizon.date)} · spot {formatPrice(shown.spot)} · expiries{" "}
            {shown.horizon.expiries_considered.map((e) => formatExpiry(e)).join(", ")}
          </p>
          {shown.warnings.map((w) => (
            <p key={w} className="idea-warning">
              {w}
            </p>
          ))}
          {shown.results.length === 0 && (
            <p className="widget-empty">
              No structure within the listed strikes pays off at this target under these limits. The line below says
              where the candidates went.
            </p>
          )}
          <ul className="idea-list">
            {shown.results.map((r) => (
              <ResultCard key={`${r.strategy}-${r.expiry}-${r.legs_label}`} r={r} range={shownRange} onLoad={onLoad} />
            ))}
          </ul>
          {shown.rejected.length > 0 && (
            <ul className="idea-rejected">
              {shown.rejected.map((rej, i) => (
                <li key={`${rej.strategy}-${rej.legs_label}-${i}`}>
                  <strong>{rej.strategy_label}</strong> {rej.legs_label} ({rej.expiry}) — {rej.rejected_because}
                </li>
              ))}
            </ul>
          )}
          <p className="optimizer-skipped">{skippedLine(shown.skipped)}</p>
          <p className="idea-disclaimer">{shown.disclaimer}</p>
        </>
      )}
    </div>
  );
}

function labelFor(s: Strategy): string {
  const short: Partial<Record<Strategy, string>> = {
    long_call: "Long call",
    long_put: "Long put",
    long_straddle: "Straddle",
    long_strangle: "Strangle",
    bull_call: "Bull call",
    bear_put: "Bear put",
    bull_put: "Bull put",
    bear_call: "Bear call",
    iron_condor: "Iron condor",
    iron_butterfly: "Iron fly",
    call_butterfly: "Call fly",
    put_butterfly: "Put fly",
    calendar: "Calendar",
    covered_call: "Covered call",
    cash_secured_put: "Cash-sec. put",
  };
  return short[s] ?? s;
}
