import { useMemo, useState } from "react";

import type { ChainResponse } from "../../types/options";

/**
 * The expiry's whole volatility curve: implied vol against strike, calls
 * and puts as their own lines.
 *
 * The chain already carries an IV per side per strike, so this reads what
 * is on screen rather than fetching anything. What it adds is shape. A
 * single skew number (app/options/iv_context.py) answers "which side is
 * dearer" and nothing else; the curve shows whether that gap holds across
 * the chain or rests on one strike, where the smile turns up, and -- the
 * reason this earns its place next to an earnings screen -- when a side's
 * implied vol is not a market at all but a solver's answer to a quote
 * nobody would trade against.
 *
 * The two rings mark the strikes the skew number is measured between:
 * the put and the call the same distance either side of spot.
 */

const SKEW_DISTANCE = 0.05;
/** How far either side of spot counts as near the money when measuring the
 * gap between the two sides -- mirrors iv_context.NEAR_ATM_PCT. */
const NEAR_ATM = 0.03;
const W = 560;
const H = 150;
const PAD = { l: 40, r: 10, t: 12, b: 22 };

interface Point {
  strike: number;
  iv: number;
}

function nearest(points: Point[], target: number): Point | null {
  if (points.length === 0) return null;
  return points.reduce((best, p) => (Math.abs(p.strike - target) < Math.abs(best.strike - target) ? p : best));
}

export function IvCurve({ chain }: { chain: ChainResponse }) {
  const [hover, setHover] = useState<number | null>(null);

  const model = useMemo(() => {
    const calls: Point[] = [];
    const puts: Point[] = [];
    for (const row of chain.rows) {
      if (row.call?.iv) calls.push({ strike: row.strike, iv: row.call.iv });
      if (row.put?.iv) puts.push({ strike: row.strike, iv: row.put.iv });
    }
    if (calls.length + puts.length < 4) return null;
    // What the two sides disagree by near the money. Put-call parity says
    // one strike carries one implied vol; a feed that solves both sides
    // against spot rather than the forward pushes them apart by roughly a
    // constant, which is an artefact and not a view. Measured here so it
    // can be taken back out -- see backend iv_context.parity_offset.
    const gaps: number[] = [];
    for (const row of chain.rows) {
      if (!row.call?.iv || !row.put?.iv) continue;
      if (Math.abs(row.strike - chain.spot) > chain.spot * NEAR_ATM) continue;
      gaps.push(row.call.iv - row.put.iv);
    }
    const offset = gaps.length >= 2 ? gaps.reduce((a, b) => a + b, 0) / gaps.length : null;
    // The curve the word skew actually names: out-of-the-money contracts
    // only -- puts below spot, calls above -- with the call side brought
    // onto the put side's footing, so one continuous line crosses spot.
    const smile: Point[] = [
      ...puts.filter((p) => p.strike <= chain.spot),
      ...calls.filter((p) => p.strike >= chain.spot).map((p) => ({ strike: p.strike, iv: p.iv - (offset ?? 0) })),
    ].sort((a, b) => a.strike - b.strike);
    const strikes = [...calls, ...puts].map((p) => p.strike);
    const ivs = [...calls, ...puts, ...smile].map((p) => p.iv);
    const xMin = Math.min(...strikes);
    const xMax = Math.max(...strikes);
    const yMin = Math.min(...ivs);
    const yMax = Math.max(...ivs);
    // The two strikes the skew is read between, and the number itself.
    const putMark = nearest(smile.filter((p) => p.strike <= chain.spot), chain.spot * (1 - SKEW_DISTANCE));
    const callMark = nearest(smile.filter((p) => p.strike >= chain.spot), chain.spot * (1 + SKEW_DISTANCE));
    const skew = putMark && callMark ? putMark.iv - callMark.iv : null;
    return { calls, puts, smile, offset, xMin, xMax, yMin, yMax: yMax === yMin ? yMin + 0.01 : yMax, putMark, callMark, skew };
  }, [chain]);

  if (!model) return null;
  const { calls, puts, smile, offset, xMin, xMax, yMin, yMax, putMark, callMark, skew } = model;

  const x = (strike: number) => PAD.l + ((strike - xMin) / (xMax - xMin || 1)) * (W - PAD.l - PAD.r);
  const y = (iv: number) => PAD.t + ((yMax - iv) / (yMax - yMin)) * (H - PAD.t - PAD.b);
  const path = (points: Point[]) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.strike).toFixed(1)},${y(p.iv).toFixed(1)}`).join(" ");
  const pct = (iv: number) => `${(iv * 100).toFixed(0)}%`;

  const strikes = [...new Set([...calls, ...puts].map((p) => p.strike))].sort((a, b) => a - b);
  const hovered = hover == null ? null : strikes.reduce((best, s) => (Math.abs(s - hover) < Math.abs(best - hover) ? s : best));
  const hoveredCall = hovered == null ? null : calls.find((p) => p.strike === hovered);
  const hoveredPut = hovered == null ? null : puts.find((p) => p.strike === hovered);

  return (
    <div className="iv-curve">
      <div className="iv-curve-head order-hint">
        <span className="iv-legend">
          <span className="iv-swatch skew" /> Skew
          <span className="iv-swatch call" /> Calls
          <span className="iv-swatch put" /> Puts
        </span>
        {offset != null && (
          <span
            className="iv-offset"
            title="How far the two raw sides disagree near the money. One strike carries one implied volatility, so a gap here is the feed solving both sides against spot rather than the forward -- a dividend or borrow cost, not a view. The skew line has it taken back out."
          >
            sides {offset >= 0 ? "+" : ""}
            {(offset * 100).toFixed(1)} pts apart
          </span>
        )}
        {skew != null && (
          <span
            title={`The skew line's own tilt: out-of-the-money put implied vol minus out-of-the-money call implied vol, ${(SKEW_DISTANCE * 100).toFixed(0)} % either side of spot -- the ringed points. Positive is the usual equity shape, where downside insurance costs more.`}
          >
            skew {skew >= 0 ? "+" : ""}
            {(skew * 100).toFixed(1)} pts
          </span>
        )}
        {hovered != null && (
          <span className="iv-readout">
            {hovered} · call {hoveredCall ? pct(hoveredCall.iv) : "—"} · put {hoveredPut ? pct(hoveredPut.iv) : "—"}
            {hoveredCall && hoveredPut ? ` · ${((hoveredPut.iv - hoveredCall.iv) * 100).toFixed(1)} pts apart` : ""}
          </span>
        )}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Implied volatility by strike, calls and puts"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          setHover(xMin + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (xMax - xMin));
        }}
      >
        {[yMax, (yMax + yMin) / 2, yMin].map((v) => (
          <g key={v}>
            <line className="iv-grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
            <text className="iv-tick" x={PAD.l - 4} y={y(v) + 3} textAnchor="end">
              {pct(v)}
            </text>
          </g>
        ))}
        {[xMin, (xMin + xMax) / 2, xMax].map((s) => (
          <text key={s} className="iv-tick" x={x(s)} y={H - 6} textAnchor="middle">
            {s}
          </text>
        ))}
        <line className="iv-spot" x1={x(chain.spot)} x2={x(chain.spot)} y1={PAD.t} y2={H - PAD.b} />
        <text className="iv-tick" x={x(chain.spot)} y={PAD.t - 3} textAnchor="middle">
          spot
        </text>
        <path className="iv-line raw put" d={path(puts)} />
        <path className="iv-line raw call" d={path(calls)} />
        <path className="iv-line smile" d={path(smile)} />
        {calls.map((p) => (
          <circle key={`c${p.strike}`} className="iv-dot call" cx={x(p.strike)} cy={y(p.iv)} r={2.5} />
        ))}
        {puts.map((p) => (
          <circle key={`p${p.strike}`} className="iv-dot put" cx={x(p.strike)} cy={y(p.iv)} r={2.5} />
        ))}
        {smile.map((p) => (
          <circle key={`s${p.strike}`} className="iv-dot smile" cx={x(p.strike)} cy={y(p.iv)} r={2.5} />
        ))}
        {putMark && <circle className="iv-mark" cx={x(putMark.strike)} cy={y(putMark.iv)} r={5} />}
        {callMark && <circle className="iv-mark" cx={x(callMark.strike)} cy={y(callMark.iv)} r={5} />}
        {hovered != null && <line className="iv-cursor" x1={x(hovered)} x2={x(hovered)} y1={PAD.t} y2={H - PAD.b} />}
      </svg>
    </div>
  );
}
