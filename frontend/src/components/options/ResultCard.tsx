/**
 * One ranked structure as a card -- the Optimizer's result list and the
 * Earnings screen's picks are the same thing on screen, so they are the
 * same component. Moved out of OptimizerTab unchanged; the only reason it
 * lives in its own file is that two widgets now render it.
 */

import { useState } from "react";

import type { LoadableStructure, OptimizerResult, Payoff } from "../../types/options";
import { packageDragProps, symbolDragProps } from "../../utils/dragSymbol";
import { formatMoney, formatNum, formatPrice } from "../../utils/format";
import { formatExpiry, weekdayOf } from "../../utils/occ";

/** "Buy 486C · Sell 496C" from the backend's "+486C −496C". */
export function legsSentence(label: string): string {
  return label
    .split(" ")
    .map((part) => {
      const sign = part[0];
      const rest = part.slice(1);
      return `${sign === "+" ? "Buy" : "Sell"} ${rest}`;
    })
    .join(" · ");
}

/** The legs line, each leg a drag source for its own contract -- the
 * premium chart of the leg you are looking at, with no modifier to hold.
 * The label's parts and the previewed legs are built from the same list
 * in the same order; if that ever stops being true the line falls back to
 * plain text rather than labelling a leg with the wrong contract. */
function Legs({ label, legs }: { label: string; legs: { symbol: string }[] }) {
  const parts = label.split(" ");
  if (parts.length !== legs.length) return <>{legsSentence(label)}</>;
  return (
    <>
      {parts.map((part, i) => (
        <span key={legs[i].symbol}>
          {i > 0 ? " · " : ""}
          <span
            className="order-contract"
            title={`Drag onto the chart for ${legs[i].symbol}'s premium`}
            {...symbolDragProps(legs[i].symbol)}
          >
            {part[0] === "+" ? "Buy" : "Sell"} {part.slice(1)}
          </span>
        </span>
      ))}
    </>
  );
}

/** A metric coloured against the best of the list: the best is green, the
 * rest fade toward amber and then grey -- OptionStrat's convention. */
export function tier(value: number, best: number): "top" | "mid" | "low" {
  if (best <= 0) return "low";
  const ratio = value / best;
  return ratio >= 0.9 ? "top" : ratio >= 0.5 ? "mid" : "low";
}

/** The card's payoff: P/L at expiry over the price grid, green above zero
 * and red below, the breakevens dotted, the target amber, the spot blue. */
export function MiniPayoff({ payoff, targets }: { payoff: Payoff; targets: number[] }) {
  const W = 260;
  const H = 110;
  const PAD = { l: 34, r: 8, t: 8, b: 16 };
  const xs = payoff.prices;
  const ys = payoff.at_expiry;
  const xMin = xs[0];
  const xMax = xs[xs.length - 1];
  const yMin = Math.min(0, ...ys);
  const yMaxRaw = Math.max(0, ...ys);
  const yMax = yMaxRaw === yMin ? yMin + 1 : yMaxRaw;
  const x = (p: number) => PAD.l + ((p - xMin) / (xMax - xMin)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((yMax - v) / (yMax - yMin)) * (H - PAD.t - PAD.b);
  const path = ys.map((v, i) => `${i === 0 ? "M" : "L"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  const area = (above: boolean) => {
    const clipped = ys.map((v) => (above ? Math.max(v, 0) : Math.min(v, 0)));
    const p = clipped.map((v, i) => `${i === 0 ? "M" : "L"}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    return `${p} L${x(xMax).toFixed(1)},${zero.toFixed(1)} L${x(xMin).toFixed(1)},${zero.toFixed(1)} Z`;
  };
  const ticks = [xMin, (xMin + xMax) / 2, xMax];
  return (
    <svg className="opt-mini" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="P/L at expiry">
      <defs>
        <linearGradient id="opt-up" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--delta-up)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--delta-up)" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="opt-down" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="var(--delta-down)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--delta-down)" stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <path d={area(true)} fill="url(#opt-up)" />
      <path d={area(false)} fill="url(#opt-down)" />
      <line className="opt-mini-zero" x1={PAD.l} x2={W - PAD.r} y1={zero} y2={zero} />
      <text className="opt-mini-tick" x={PAD.l - 3} y={y(yMax) + 4} textAnchor="end">
        {formatNum(yMax, 0)}
      </text>
      <text className="opt-mini-tick" x={PAD.l - 3} y={zero + 4} textAnchor="end">
        0
      </text>
      {yMin < 0 && (
        <text className="opt-mini-tick" x={PAD.l - 3} y={y(yMin) + 4} textAnchor="end">
          {formatNum(yMin, 0)}
        </text>
      )}
      {ticks.map((p) => (
        <text key={p} className="opt-mini-tick" x={x(p)} y={H - 4} textAnchor="middle">
          {p.toFixed(p < 10 ? 2 : 0)}
        </text>
      ))}
      <line className="opt-mini-spot" x1={x(payoff.spot)} x2={x(payoff.spot)} y1={PAD.t} y2={H - PAD.b} />
      {targets
        .filter((t) => t >= xMin && t <= xMax)
        .map((t) => (
          <line key={t} className="opt-mini-target" x1={x(t)} x2={x(t)} y1={PAD.t} y2={H - PAD.b} />
        ))}
      {payoff.breakevens
        .filter((b) => b >= xMin && b <= xMax)
        .map((b) => (
          <g key={b}>
            <line className="opt-mini-be" x1={x(b)} x2={x(b)} y1={PAD.t} y2={H - PAD.b} />
            <text className="opt-mini-be-label" x={x(b)} y={PAD.t + 8} textAnchor="middle">
              {b.toFixed(2)}
            </text>
          </g>
        ))}
      <path className="opt-mini-line" d={path} />
    </svg>
  );
}

export function ResultCard({
  r,
  bestRor,
  bestChance,
  targets,
  onLoad,
}: {
  r: OptimizerResult;
  bestRor: number;
  bestChance: number;
  targets: number[];
  onLoad: (s: LoadableStructure) => boolean;
}) {
  const [failed, setFailed] = useState(false);
  const rorPct = r.return_on_risk * 100;
  // A structure has both an underlying and contracts, so it drags like an
  // option package: plain onto the chart for the stock, shift-drag for the
  // first leg's premium. Same gesture as a resting or held package.
  const contract = r.spread.legs.find((leg) => leg.symbol)?.symbol ?? null;
  return (
    <li
      className="opt-card"
      title="Drag onto the chart for the underlying; hold ⇧ while dragging for the first leg's premium"
      {...packageDragProps(r.spread.underlying, contract)}
    >
      <div className="opt-card-title">{r.strategy_label}</div>
      <div className="opt-card-legs">
        <Legs label={r.legs_label} legs={r.spread.legs} /> · {weekdayOf(r.expiry)} {formatExpiry(r.expiry)}
      </div>
      <div className="opt-card-stats">
        <span
          className={`opt-stat ror ${tier(r.return_on_risk, bestRor)}`}
          title="P/L at the worst point of the target divided by what the account puts up -- the debit paid, or a credit structure's collateral. Not a probability."
        >
          <strong>{rorPct.toFixed(0)}%</strong> Return on risk
        </span>
        <span
          className={`opt-stat chance ${r.chance != null && bestChance > 0 ? tier(r.chance, bestChance) : "low"}`}
          title="Share of the option market's own implied distribution (at-the-money IV, lognormal, no drift) under which the position is profitable on the horizon date. A model number, not a forecast."
        >
          <strong>{r.chance == null ? "—" : `${(r.chance * 100).toFixed(0)}%`}</strong> Chance
        </span>
        <span className="opt-stat plain" title="P/L if the underlying is at the target on the horizon date, each leg's IV unchanged (with a range: its worst point).">
          <strong className={r.pnl_min >= 0 ? "delta-up" : "delta-down"}>{formatMoney(r.pnl_min)}</strong> Profit
        </span>
        <span className="opt-stat plain" title="What the account puts up: the debit paid, or the collateral of a credit structure.">
          <strong>{formatMoney(r.risk)}</strong> Risk
        </span>
      </div>
      {r.spread.payoff ? <MiniPayoff payoff={r.spread.payoff} targets={targets} /> : null}
      <div className="opt-card-foot">
        <span className="order-hint">
          {r.direction === "debit" ? "Pay" : "Receive"} {formatMoney(Math.abs(r.net_price) * 100 * r.spread.qty)} · max{" "}
          {r.max_profit == null ? "unlimited" : formatMoney(r.max_profit)} / {r.max_loss == null ? "—" : formatMoney(r.max_loss)}
          {r.breakevens.length ? ` · BE ${r.breakevens.map((b) => formatPrice(b)).join(" / ")}` : ""}
        </span>
        <button type="button" className="generate-button opt-load" onClick={() => setFailed(!onLoad({ strategy: r.strategy, ticket: r.ticket }))}>
          Load into ticket
        </button>
      </div>
      {r.spread.warnings.map((w) => (
        <p key={w} className="idea-warning">
          {w}
        </p>
      ))}
      {failed && <p className="order-rejection">Could not load this structure into the ticket.</p>}
    </li>
  );
}
