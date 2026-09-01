import { isGexSymbol } from "../../hooks/useGexLevels";
import { useGexPlan } from "../../hooks/useGexPlan";
import type { GexPlanSymbol } from "../../types/gex";

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
    </div>
  );
}

interface GexPlanWidgetProps {
  /** The symbol currently active elsewhere on the dashboard (e.g. charted/
   * selected). GEX only exists for a fixed symbol list (see backend
   * app.market_data.gamma_exposure.SYMBOLS) -- same isGexSymbol gate
   * ChartWidget uses for its own GEX levels/badge -- so the plan only shows
   * when the active symbol is one of them. */
  symbol: string | null;
}

/**
 * Rule-based GEX playbook for whichever covered symbol is currently active
 * elsewhere on the dashboard -- see backend app.market_data.gex_plan.
 * Descriptive, not advisory (same framing as the market-conditions badge):
 * states what the standard GEX heuristics say a regime like this tends to
 * mean, not a buy/sell call.
 */
export function GexPlanWidget({ symbol }: GexPlanWidgetProps) {
  const symbolHasGex = isGexSymbol(symbol);
  const plan = useGexPlan();

  return (
    <div className="widget gex-plan-widget">
      <div className="widget-header">
        <h2>GEX Plan</h2>
      </div>
      <div className="widget-body">
        {!symbolHasGex ? (
          <div className="widget-empty">Select SPY, QQQ, TSLA, NVDA, or PLTR to see its GEX plan.</div>
        ) : !plan || !plan.available ? (
          <div className="widget-empty">
            GEX not available yet -- needs Alpaca credentials and the first refresh to complete.
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
