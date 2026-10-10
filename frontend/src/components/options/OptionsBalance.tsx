import { useState } from "react";

import { useBalanceHistory } from "../../hooks/useBalanceHistory";
import type { ClosedSpread } from "../../types/options";
import type { BalancePoint, BalanceRange } from "../../types/trading";
import { BalanceChart } from "../trading/BalanceChart";
import { BalancePanel } from "../trading/TradingWidget";

/** The closed spreads' realised P/L added up over time, as balance points:
 * a 0 a minute before the first close, so the chart's baseline is "nothing made yet",
 * then one point per close. lightweight-charts wants strictly increasing
 * times, and spreads that settle together (two 0DTE at the same 16:00) share
 * a second -- each repeat is moved a second on. */
export function cumulativePoints(spreads: ClosedSpread[]): BalancePoint[] {
  const sorted = [...spreads].sort((a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at));
  if (sorted.length === 0) return [];
  // A minute before the first close: the curve is about closes, and the
  // first spread's opening (weeks earlier, maybe) would only stretch the axis.
  const first = Math.floor(Date.parse(sorted[0].closed_at) / 1000) - 60;
  const out: BalancePoint[] = [{ t: first, equity: 0, profit_loss: 0, profit_loss_pct: null }];
  let total = 0;
  for (const s of sorted) {
    total = Math.round((total + s.pnl) * 100) / 100;
    let t = Math.floor(Date.parse(s.closed_at) / 1000);
    if (t <= out[out.length - 1].t) t = out[out.length - 1].t + 1;
    out.push({ t, equity: total, profit_loss: s.pnl, profit_loss_pct: null });
  }
  return out;
}

/** The Closed tab's balance section: the account's equity curve (the
 * Trading widget's own chart and ranges) or the options' realised P/L added
 * up, for the spreads of the range the tab shows. Collapsed until opened,
 * so the broker's portfolio history is only fetched while it is in view. */
export function OptionsBalance({ spreads, rangeLabel }: { spreads: ClosedSpread[]; rangeLabel: string }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"account" | "options">("account");
  const [range, setRange] = useState<BalanceRange>("1M");
  const { history, loading, error } = useBalanceHistory(range, open && view === "account");
  const points = cumulativePoints(spreads);
  const total = points.length ? points[points.length - 1].equity : 0;

  return (
    <section className="options-balance">
      <div className="pb-start-head">
        <button type="button" className="timeframe-button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          Balance {open ? "▾" : "▸"}
        </button>
        {open && (
          <span className="timeframe-selector" role="group" aria-label="Balance view">
            <button type="button" className="timeframe-button" aria-pressed={view === "account"} onClick={() => setView("account")}
              title="The whole account's equity over time -- stocks and options together, as the broker reports it">
              Account
            </button>
            <button type="button" className="timeframe-button" aria-pressed={view === "options"} onClick={() => setView("options")}
              title="The closed option spreads' realised P/L added up -- the options alone, for the range chosen above">
              Options P/L
            </button>
          </span>
        )}
      </div>
      {open && view === "account" && (
        <BalancePanel range={range} onRangeChange={setRange} history={history} loading={loading} error={error} />
      )}
      {open && view === "options" && (
        <div className="trading-subview">
          <div className="trading-balance-chart">
            {points.length < 2 ? (
              <div className="widget-empty">No closed option spread in this range yet.</div>
            ) : (
              <BalanceChart points={points} daily={false} />
            )}
          </div>
          <p className="order-hint">
            Realised P/L of the closed spreads ({rangeLabel}):{" "}
            <strong className={total > 0 ? "delta-up" : total < 0 ? "delta-down" : undefined}>
              {total >= 0 ? "+" : "−"}${Math.abs(total).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </strong>{" "}
            over {spreads.length} spread{spreads.length === 1 ? "" : "s"}. Open positions are not in it; the account curve has them at their marks.
          </p>
        </div>
      )}
    </section>
  );
}
