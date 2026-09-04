import { useGexPlan } from "../../hooks/useGexPlan";
import type { GexPlanSymbol, GexSupport } from "../../types/gex";

/** Below this the profile rests on so little that saying so alongside the
 * numbers is the honest thing -- it is a legibility hint, not a threshold
 * that suppresses anything. Both halves matter: a dozen strikes with no
 * open interest on them is as thin as three strikes with plenty. */
const THIN_CONTRACTS = 20;
const THIN_OPEN_INTEREST = 5_000;

export function isThin(support: GexSupport): boolean {
  return support.contracts_used < THIN_CONTRACTS || support.open_interest_used < THIN_OPEN_INTEREST;
}

export function supportLabel(support: GexSupport): string {
  return `${support.contracts_used} strikes \u00b7 ${support.open_interest_used.toLocaleString()} open interest`;
}

function SymbolPlan({ symbol, plan }: { symbol: string; plan: GexPlanSymbol | undefined }) {
  if (!plan) {
    return (
      <div className="gex-plan-symbol">
        <div className="gex-plan-symbol-header">
          <span className="gex-plan-symbol-name">{symbol}</span>
        </div>
        <div className="widget-empty">No reading yet</div>
      </div>
    );
  }

  return (
    <div className="gex-plan-symbol">
      <div className="gex-plan-symbol-header">
        <span className="gex-plan-symbol-name">{symbol}</span>
        <span className="gex-plan-badge" data-level={plan.regime}>
          <span className="gex-plan-dot" />
          {plan.regime === "positive" ? "Positive gamma" : "Negative gamma"}
        </span>
        {plan.near_flip && <span className="stale-badge">NEAR FLIP</span>}
      </div>
      <div className="gex-plan-levels">
        {plan.gamma_flip_strike != null && (
          <span>
            Flip <strong>{plan.gamma_flip_strike.toFixed(2)}</strong>
          </span>
        )}
        {plan.call_wall && (
          <span>
            Call wall <strong>{plan.call_wall.strike.toFixed(2)}</strong>
          </span>
        )}
        {plan.put_wall && (
          <span>
            Put wall <strong>{plan.put_wall.strike.toFixed(2)}</strong>
          </span>
        )}
      </div>
      <ul className="gex-plan-playbook">
        {plan.playbook.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {/* What the profile rests on, always -- and a plain warning when
        * that is little. A gamma wall built from a handful of contracts is
        * still a real number; it just isn't the same kind of evidence as
        * one built from tens of thousands of open contracts. */}
      <div className="gex-plan-support" data-thin={isThin(plan) || undefined}>
        {isThin(plan) ? "Thin chain -- " : ""}
        {supportLabel(plan)}
      </div>
    </div>
  );
}

interface GexPlanWidgetProps {
  /** The symbol currently active elsewhere on the dashboard (e.g. charted/
   * selected). Any optionable symbol works: the backend computes a reading
   * on request rather than only for a precomputed list. */
  symbol: string | null;
}

/**
 * Rule-based GEX playbook for whichever symbol is currently active
 * elsewhere on the dashboard -- see backend app.market_data.gex_plan.
 * Descriptive, not advisory (same framing as the market-conditions badge):
 * states what the standard GEX heuristics say a regime like this tends to
 * mean, not a buy/sell call.
 */
export function GexPlanWidget({ symbol }: GexPlanWidgetProps) {
  const { plan, loading } = useGexPlan(symbol);

  return (
    <div className="widget gex-plan-widget">
      <div className="widget-header">
        <h2>GEX Plan</h2>
      </div>
      <div className="widget-body">
        {!symbol ? (
          <div className="widget-empty">Select a symbol to see its GEX plan.</div>
        ) : loading ? (
          // A symbol the backend has not computed before takes seconds, not
          // milliseconds -- saying so beats an empty state that reads as
          // "this symbol has no GEX".
          <div className="widget-empty">Reading {symbol}&rsquo;s options chain&hellip;</div>
        ) : !plan || !plan.available ? (
          <div className="widget-empty">
            No GEX for {symbol} -- it needs Alpaca credentials and a listed options chain.
          </div>
        ) : (
          <div className="gex-plan-grid">
            <SymbolPlan symbol={symbol} plan={plan.symbols[symbol]} />
          </div>
        )}
      </div>
    </div>
  );
}
