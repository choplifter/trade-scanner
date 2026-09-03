import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

import type { Payoff } from "../../types/options";
import { formatNum } from "../../utils/format";
import { getSettings, updateSettings } from "../../api/settings";
import { useSettings } from "../../hooks/useSettings";

const WIDTH = 560;
const HEIGHT = 200;
const PAD = { top: 14, right: 12, bottom: 24, left: 52 };

function money(value: number): string {
  return formatNum(value, 0);
}

interface PayoffChartProps {
  payoff: Payoff;
  /** What to call the at-expiry line: "at expiry" or, for a calendar, the
   * short leg's expiry. */
  expiryLabel?: string;
}

/** The risk chart: P&L (y) over the underlying's price (x). A solid line
 * at expiry with the profit and loss areas tinted, a dashed line for
 * today's model value, the zero line, the spot, the breakevens. Plain
 * SVG: the numbers all come from the backend (app/options/payoff.py). */
export function PayoffChart({ payoff, expiryLabel }: PayoffChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  // The frame is CSS-resizable (drag its bottom edge); the height it ends
  // up at is remembered in the settings so every risk chart shares it.
  const [settings] = useSettings();
  const frameRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame || typeof ResizeObserver === "undefined") return;
    let timer: number | null = null;
    const observer = new ResizeObserver(() => {
      const height = Math.round(frame.getBoundingClientRect().height);
      if (!height || height === getSettings().riskChartHeight) return;
      if (timer != null) window.clearTimeout(timer);
      timer = window.setTimeout(() => updateSettings({ riskChartHeight: height }), 250);
    });
    observer.observe(frame);
    return () => {
      observer.disconnect();
      if (timer != null) window.clearTimeout(timer);
    };
  }, []);

  const geometry = useMemo(() => {
    const xs = payoff.prices;
    const ys = payoff.today ? [...payoff.at_expiry, ...payoff.today] : payoff.at_expiry;
    const xMin = xs[0];
    const xMax = xs[xs.length - 1];
    let yMin = Math.min(0, ...ys);
    let yMax = Math.max(0, ...ys);
    if (yMax === yMin) {
      yMax += 1;
      yMin -= 1;
    }
    const padY = (yMax - yMin) * 0.08;
    yMin -= padY;
    yMax += padY;
    const innerW = WIDTH - PAD.left - PAD.right;
    const innerH = HEIGHT - PAD.top - PAD.bottom;
    const x = (price: number) => PAD.left + ((price - xMin) / (xMax - xMin)) * innerW;
    const y = (pnl: number) => PAD.top + ((yMax - pnl) / (yMax - yMin)) * innerH;
    const path = (values: number[]) => values.map((v, i) => `${i === 0 ? "M" : "L"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    // The tinted areas: the at-expiry curve clipped to above / below zero.
    const zeroY = y(0);
    const area = (values: number[], above: boolean) => {
      const clipped = values.map((v) => (above ? Math.max(v, 0) : Math.min(v, 0)));
      return `${path(clipped)} L${x(xs[xs.length - 1]).toFixed(1)},${zeroY.toFixed(1)} L${x(xs[0]).toFixed(1)},${zeroY.toFixed(1)} Z`;
    };
    const ticksX = [xMin, (xMin + xMax) / 2, xMax];
    const ticksY = [yMax - padY, 0, yMin + padY].filter((v, i, arr) => arr.indexOf(v) === i);
    return { x, y, path, area, zeroY, ticksX, ticksY, xMin, xMax };
  }, [payoff]);

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * WIDTH;
    let best = 0;
    for (let i = 1; i < payoff.prices.length; i++) {
      if (Math.abs(geometry.x(payoff.prices[i]) - px) < Math.abs(geometry.x(payoff.prices[best]) - px)) best = i;
    }
    setHover(best);
  };

  const { x, y, path, area, zeroY, ticksX, ticksY } = geometry;
  const hovered = hover != null ? { price: payoff.prices[hover], expiry: payoff.at_expiry[hover], today: payoff.today?.[hover] ?? null } : null;

  return (
    <div className="payoff-chart">
      <div className="payoff-frame" ref={frameRef} style={{ height: settings.riskChartHeight }} title="Drag the bottom edge to resize">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="Profit and loss over the underlying price"
      >
        <path className="payoff-area-profit" d={area(payoff.at_expiry, true)} />
        <path className="payoff-area-loss" d={area(payoff.at_expiry, false)} />
        {ticksY.map((v) => (
          <g key={`y${v}`}>
            <line className="payoff-grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(v)} y2={y(v)} />
            <text className="payoff-tick" x={PAD.left - 4} y={y(v) + 3} textAnchor="end">
              {money(v)}
            </text>
          </g>
        ))}
        <line className="payoff-zero" x1={PAD.left} x2={WIDTH - PAD.right} y1={zeroY} y2={zeroY} />
        {ticksX.map((v) => (
          <text key={`x${v}`} className="payoff-tick" x={x(v)} y={HEIGHT - 8} textAnchor="middle">
            {v.toFixed(v < 10 ? 2 : 1)}
          </text>
        ))}
        {payoff.today && <path className="payoff-today" d={path(payoff.today)} />}
        <path className="payoff-expiry" d={path(payoff.at_expiry)} />
        <line className="payoff-spot" x1={x(payoff.spot)} x2={x(payoff.spot)} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
        <text className="payoff-tick payoff-spot-label" x={x(payoff.spot)} y={PAD.top - 3} textAnchor="middle">
          spot {payoff.spot.toFixed(2)}
        </text>
        {payoff.breakevens.map((be) => (
          <g key={be}>
            <circle className="payoff-breakeven" cx={x(be)} cy={zeroY} r={3.5} />
            <text className="payoff-tick" x={x(be)} y={zeroY - 6} textAnchor="middle">
              {be.toFixed(2)}
            </text>
          </g>
        ))}
        {hovered && (
          <g>
            <line className="payoff-hover" x1={x(hovered.price)} x2={x(hovered.price)} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
            <circle className="payoff-hover-dot" cx={x(hovered.price)} cy={y(hovered.expiry)} r={3} />
            {hovered.today != null && <circle className="payoff-hover-dot today" cx={x(hovered.price)} cy={y(hovered.today)} r={3} />}
          </g>
        )}
      </svg>
      </div>
      <div className="payoff-legend">
        <span>
          <i className="payoff-swatch expiry" /> {expiryLabel ?? "at expiry"}
        </span>
        {payoff.today ? (
          <span>
            <i className="payoff-swatch today" /> today (model)
          </span>
        ) : (
          <span className="order-hint">no IV: expiry curve only</span>
        )}
        <span>max profit {payoff.max_profit == null ? "unlimited" : money(payoff.max_profit)}</span>
        <span>max loss {payoff.max_loss == null ? "unbounded" : money(payoff.max_loss)}</span>
        {hovered && (
          <span className="payoff-readout">
            at {hovered.price.toFixed(2)}: {money(hovered.expiry)}
            {hovered.today != null ? ` / today ${money(hovered.today)}` : ""}
          </span>
        )}
      </div>
    </div>
  );
}
