import { useState } from "react";

import { modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreads } from "../../hooks/useSpreads";
import { BrokerMissing } from "../common/BrokerMissing";
import { OpenSpreads } from "./OpenSpreads";
import { OptionOrders } from "./OptionOrders";
import { PayoffChart } from "./PayoffChart";
import { RollTicket, type RollTarget } from "./RollTicket";
import { ScreenerTab } from "./ScreenerTab";
import { TrackRecordTab } from "./TrackRecordTab";
import { useTicketPayoff } from "./ticketPayoff";

/**
 * Parts of the Options widget as panels of their own, for the dock only:
 * the risk chart large beside the chain, the screener in its own tab or
 * window, the open spreads in view while the ticket is in use. The
 * Options widget keeps every one of them as a tab too -- these are extra
 * places to put them, not a replacement.
 */

/** The ticket's risk chart, filling the panel. Follows whatever structure
 * the spread ticket shows (see ticketPayoff.ts). */
export function OptionsRiskPanel() {
  const ticket = useTicketPayoff();
  return (
    <div className="widget options-risk-panel">
      <div className="widget-header">
        <h2>Risk chart</h2>
        {ticket && (
          <span className="widget-count">
            {ticket.symbol} · {ticket.title}
          </span>
        )}
      </div>
      <div className="widget-body options-risk-body">
        {ticket ? (
          <PayoffChart payoff={ticket.payoff} expiryLabel={ticket.expiryLabel} fill />
        ) : (
          <div className="widget-empty">
            Build a structure on the Options widget's ticket -- its risk chart shows here, at the size of this panel.
          </div>
        )}
      </div>
    </div>
  );
}

export function OptionsScreenerPanel({ onSelectSymbol }: { onSelectSymbol?: (symbol: string) => void }) {
  return (
    <div className="widget options-widget">
      <div className="widget-header">
        <h2>Options screener</h2>
      </div>
      <div className="widget-body">
        <ScreenerTab onSelectSymbol={onSelectSymbol} />
      </div>
    </div>
  );
}

/** Working orders and held structures with their close, trigger and roll
 * controls -- its own poll of the book, so it runs while the Options
 * widget shows another tab. */
export function OptionsSpreadsPanel({
  symbol,
  mode,
  onSelectSymbol,
}: {
  symbol: string | null;
  mode: TradingMode;
  onSelectSymbol?: (symbol: string) => void;
}) {
  const spreads = useSpreads(true);
  const [rollTarget, setRollTarget] = useState<RollTarget | null>(null);
  const badge = modeBadge(mode);
  const count = spreads.spreads.length;
  return (
    <div className={`widget options-widget${mode === "live" ? " live-frame" : ""}`}>
      <div className="widget-header">
        <h2>Open spreads{count > 0 ? ` (${count})` : ""}</h2>
        <span className={`trading-mode-badge ${badge.className}`}>{badge.label}</span>
        <RollTicket
          target={rollTarget}
          mode={mode}
          onRolled={() => {
            setRollTarget(null);
            spreads.refresh();
          }}
          onClose={() => setRollTarget(null)}
        />
      </div>
      <div className="widget-body">
        {mode !== "simulation" && spreads.brokerMissing ? (
          <BrokerMissing mode={mode} />
        ) : (
          <>
            <OptionOrders mode={mode} onChanged={spreads.afterAction} onSelectSymbol={onSelectSymbol} />
            <OpenSpreads
              spreads={spreads.spreads}
              triggers={spreads.triggers}
              totals={spreads.totals}
              account={spreads.account}
              mode={mode}
              symbol={symbol}
              loading={spreads.loading}
              error={spreads.error}
              onClose={spreads.close}
              onArm={spreads.armTrigger}
              onCancelTrigger={spreads.cancelTrigger}
              onSelectSymbol={onSelectSymbol}
              onRoll={(group, preset) => setRollTarget({ group, ...preset })}
            />
          </>
        )}
      </div>
    </div>
  );
}

export function OptionsRecordPanel() {
  return (
    <div className="widget options-widget">
      <div className="widget-header">
        <h2>Prediction record</h2>
      </div>
      <div className="widget-body">
        <TrackRecordTab />
      </div>
    </div>
  );
}
