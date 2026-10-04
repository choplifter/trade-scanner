import type { OptionsAccountResponse, PositionGreeks, SpreadTotals, VolForecast } from "../../types/options";
import { formatMoney } from "../../utils/format";
import { NATENBERG, withBook } from "./bookRefs";

function signed(value: number, digits: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** A position's greeks in the units it is managed in -- backend
 * app/options/position_risk.py. Vega first among equals: for the short
 * premium shapes it is the number nothing else on screen shows. */
export function GreeksLine({ greeks }: { greeks: PositionGreeks }) {
  return (
    <span className="position-greeks">
      <span
        title={withBook(
          "Delta in share equivalents: the position gains this many dollars per 1 $ rise of the underlying.",
          NATENBERG.delta,
        )}
      >
        Δ {signed(greeks.delta, 0)}
      </span>
      {greeks.skew_delta != null && Math.abs(greeks.skew_delta - greeks.delta) >= 0.5 && (
        <span
          className="vol-margin-detail"
          title={withBook(
            "The delta with the smile moving along with the stock: as the underlying rises, every strike sits lower relative to it and takes the implied volatility the smile has there, so each leg's value changes by its vega times that shift as well. Under the usual put skew implied volatility falls as the stock rises, which leaves a short-vega position shorter than the flat model says. Read off this chain's own smile.",
            NATENBERG.skewedRisk,
          )}
        >
          {" "}
          (skew {signed(greeks.skew_delta, 0)})
        </span>
      )}
      {" · "}
      <span title="Gamma: how much that delta changes per 1 $ move -- negative means the position turns against the move.">
        Γ {signed(greeks.gamma, 2)}
      </span>
      {" · "}
      <span title={withBook("Theta: dollars the position earns (+) or loses (−) per calendar day at a standing price.", NATENBERG.theta)}>
        Θ {signed(greeks.theta, 0)} $/day
      </span>
      {" · "}
      <span
        title={withBook(
          "Vega: dollars the position gains (+) or loses (−) when implied volatility rises one point. A condor or a credit spread is short vega: a volatility spike costs it this much per point before the stock has moved at all.",
          NATENBERG.vega,
        )}
      >
        V {signed(greeks.vega, 0)} $/pt
      </span>
    </span>
  );
}

/** Natenberg's margin for error: the volatility the price implies (the
 * level at which the model values the package at the limit) against the
 * volatility expected over the position's life -- the 20-session reading
 * blended toward the past year's (ch. 20, mean reversion). A seller (vega
 * below zero) wants the forecast below the breakeven, a buyer above it;
 * the gap is how wrong that forecast can be before the trade has no edge. */
export function VolMargin({
  breakevenVol,
  forecast,
  realisedVol,
  vega,
}: {
  breakevenVol: number;
  forecast: VolForecast | null;
  realisedVol: number | null;
  vega: number | null;
}) {
  const seller = vega == null ? null : vega < 0;
  const expected = forecast?.forecast ?? realisedVol;
  const margin =
    expected == null || seller == null ? null : (seller ? breakevenVol - expected : expected - breakevenVol) * 100;
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const blend =
    forecast && forecast.long_run != null
      ? ` The forecast blends the 20-session reading (${pct(forecast.recent)}) with the past year's (${pct(forecast.long_run)}), ${Math.round(forecast.weight_recent * 100)} % on the recent one: volatility drifts back to its long-run level, and the longer the option runs the more of that drift it lives through.`
      : forecast
        ? " Less than half a year of closes: the forecast is the 20-session reading alone."
        : "";
  const title = withBook(
    `The volatility at which the model values this package at its limit: ${pct(breakevenVol)} at the money. ` +
      (seller == null
        ? "No greeks, so no side to judge the forecast from."
        : seller
          ? "Selling volatility, the trade has an edge while the stock moves less than that; the margin is how far the volatility may come out above the forecast before it is gone."
          : "Buying volatility, the trade has an edge while the stock moves more than that; the margin is how far the volatility may come out below the forecast before it is gone.") +
      blend +
      " A better limit moves the breakeven your way; crossing the spread moves it against you. A forecast, not a promise.",
    NATENBERG.marginForError,
    NATENBERG.volForecasting,
  );
  return (
    <span className="vol-margin" title={title}>
      Breakeven vol {pct(breakevenVol)}
      {expected != null && (
        <>
          {" · "}
          {forecast ? "forecast" : "realised"} {pct(expected)}
          {forecast && forecast.long_run != null && (
            <span className="vol-margin-detail">
              {" "}
              (20d {pct(forecast.recent)}, 1y {pct(forecast.long_run)})
            </span>
          )}
        </>
      )}
      {margin != null && (
        <>
          {" · margin "}
          <span className={margin >= 0 ? "vol-margin-ok" : "vol-margin-bad"}>{signed(margin, 1)} pts</span>
        </>
      )}
    </span>
  );
}

/** What the open option structures tie up, over the Open spreads list:
 * the structures' collateral (backend position_risk.spread_risks) against
 * the account's free options buying power and its equity, read off the
 * account the widget already polls. */
export function MarginBar({ totals, account }: { totals: SpreadTotals; account: OptionsAccountResponse | null }) {
  const tied = totals.collateral;
  const free = account?.options_buying_power ?? null;
  const equity = account?.equity ?? null;
  const total = free != null ? tied + free : null;
  const used = total && total > 0 ? tied / total : null;
  const shareOfEquity = equity ? tied / equity : null;
  const greeksPartial = totals.structures < totals.of;
  return (
    <div className="margin-bar">
      <div className="margin-bar-text">
        <span
          title="What the open structures tie up: each credit spread's wider wing less its credit, a cash-secured put's strike; debits and covered calls tie up nothing beyond what was paid or held. The broker's own requirement for these shapes, estimated from the legs."
        >
          Margin {formatMoney(tied)}
        </span>
        {free != null && <span title="Options buying power still free for new structures.">free {formatMoney(free)}</span>}
        {shareOfEquity != null && (
          <span title="The tied-up margin as a share of the account's equity.">
            {(shareOfEquity * 100).toFixed(1)}% of equity
          </span>
        )}
        {totals.structures > 0 && (
          <span
            title={withBook(
              "Summed over every structure with quotes: what the book earns per day at standing prices, and what one point of implied volatility does to it." +
                (greeksPartial ? ` ${totals.of - totals.structures} structure(s) had no IV and are left out.` : ""),
              NATENBERG.positionAnalysis,
            )}
          >
            book Θ {signed(totals.theta, 0)} $/day · V {signed(totals.vega, 0)} $/pt
            {greeksPartial ? ` (${totals.structures} of ${totals.of})` : ""}
          </span>
        )}
      </div>
      {used != null && (
        <div className="margin-bar-track" aria-hidden="true">
          <div className={`margin-bar-fill${used > 0.8 ? " margin-bar-high" : ""}`} style={{ width: `${Math.min(100, used * 100)}%` }} />
        </div>
      )}
    </div>
  );
}
