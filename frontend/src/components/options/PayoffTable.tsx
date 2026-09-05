import { useMemo, useState } from "react";

import type { Payoff } from "../../types/options";
import { dateGrid, positionPnl, priceGrid } from "../../utils/blackScholes";
import { formatNum } from "../../utils/format";
import { HEATMAP_STRONG_THRESHOLD, heatmapBlendWeight, heatmapFill } from "../../utils/heatmapColor";
import { formatClock, formatWeekdayDate, formatWeekdayDateTime } from "../../utils/time";

interface PayoffTableProps {
  payoff: Payoff;
  /** The IV slider's factor, shared with the chart view. */
  ivFactor: number;
  /** What the last column is called: "expiry", or a calendar's short expiry. */
  expiryLabel?: string;
}

type CellMode = "money" | "pct_risk";

/**
 * OptionStrat's profit/loss table: rows are prices of the underlying around
 * the spot, columns are the trading days from now to expiry, each cell the
 * position's P/L there -- the same Black-Scholes the chart's today line
 * uses (utils/blackScholes.ts), in the browser, so every cell is a
 * function call rather than a request. Coloured with the heatmap scale the
 * rest of the app uses, scaled to the table's own largest |P/L|.
 *
 * Time is the columns rather than a slider: the whole path of a position
 * is visible at once, which is what the table adds over the chart. The IV
 * slider still applies. Weekends are skipped, exchange holidays are not
 * (no calendar of them is loaded); each column is that day's close.
 */
export function PayoffTable({ payoff, ivFactor, expiryLabel }: PayoffTableProps) {
  const [mode, setMode] = useState<CellMode>("money");
  const risk = payoff.max_loss != null && payoff.max_loss < 0 ? -payoff.max_loss : null;

  const grid = useMemo(() => {
    if (!payoff.as_of) return null;
    const asOfMs = Date.parse(payoff.as_of);
    const dates = dateGrid(asOfMs, payoff.expiry);
    const prices = priceGrid(payoff);
    const cells: (number | null)[][] = prices.map((price) => dates.map((atMs) => positionPnl(payoff, price, atMs, ivFactor)));
    if (cells.some((row) => row.some((v) => v == null))) return null;
    let maxAbs = 1;
    for (const row of cells) for (const v of row) maxAbs = Math.max(maxAbs, Math.abs(v as number));
    return { dates, prices, cells: cells as number[][], maxAbs, asOfMs };
  }, [payoff, ivFactor]);

  if (!grid) return <p className="order-hint">no IV: table unavailable</p>;

  const { dates, prices, cells, maxAbs, asOfMs } = grid;
  const spotRow = prices.indexOf(payoff.spot);
  const label = (v: number) => {
    if (mode === "pct_risk" && risk) return `${((v / risk) * 100).toFixed(0)}%`;
    return formatNum(v, 0);
  };

  return (
    <div className="payoff-table-wrap">
      <div className="payoff-table-scroll">
        <table className="payoff-table">
          <thead>
            <tr>
              <th className="payoff-table-corner">price</th>
              {dates.map((atMs, i) => {
                const last = i === dates.length - 1;
                const title = i === 0 ? `today ${formatClock(atMs)}` : formatWeekdayDateTime(atMs);
                return (
                  <th key={atMs} className={last ? "expiry" : undefined} title={title}>
                    {i === 0 ? "now" : last ? (expiryLabel ?? "expiry") : formatWeekdayDate(atMs)}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {prices.map((price, r) => (
              <tr key={price} className={r === spotRow ? "spot" : undefined}>
                <th title={r === spotRow ? "spot" : undefined}>{price.toFixed(price < 10 ? 2 : 1)}</th>
                {cells[r].map((v, c) => {
                  const strong = heatmapBlendWeight(v, maxAbs) > HEATMAP_STRONG_THRESHOLD;
                  return (
                    <td
                      key={c}
                      className={strong ? "strong" : undefined}
                      style={{ background: heatmapFill(v, maxAbs) }}
                      title={`${c === 0 ? `today ${formatClock(asOfMs)}` : formatWeekdayDateTime(dates[c])} at ${price.toFixed(2)}: ${formatNum(v, 0)}`}
                    >
                      {label(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="payoff-table-modes">
        <span className="timeframe-selector">
          <button type="button" className="timeframe-button" aria-pressed={mode === "money"} onClick={() => setMode("money")}>
            $
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={mode === "pct_risk"}
            disabled={!risk}
            title={risk ? "P/L as a share of the position's defined maximum loss" : "No defined maximum loss to relate to"}
            onClick={() => setMode("pct_risk")}
          >
            % of risk
          </button>
        </span>
        <span className="order-hint">columns are closes (16:00 New York); weekends skipped, holidays not</span>
      </div>
    </div>
  );
}
