import { useMemo, useRef } from "react";
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import type { ChainResponse, OptionKind, Strategy } from "../../types/options";
import { formatStrike } from "../../utils/occ";
import { legHandles, quoted, type LegHandle, type LegHandleId, type Legs, type PickContext } from "./legPicker";

interface StrikeRailProps {
  chain: ChainResponse;
  /** The far chain of a calendar/diagonal: the long handle snaps to it. */
  longChain?: ChainResponse | null;
  strategy: Strategy;
  legs: Legs | null;
  ctx: PickContext;
  /** Move one leg to a listed strike (the widget applies moveLeg). */
  onMove: (id: LegHandleId, strike: number) => void;
  /** Move every leg by this many strikes (the widget applies shiftLegs). */
  onShift: (deltaSteps: number) => void;
}

const W = 1000;
const H = 30;
const PAD = 14;
const BASE_Y = 19;

function railRows(chain: ChainResponse, longChain: ChainResponse | null | undefined, handle: LegHandle): number[] {
  const source = handle.id === "long_strike" && longChain ? longChain : chain;
  if (handle.kind === "both") return source.rows.filter((r) => r.call && r.put).map((r) => r.strike);
  return quoted(source.rows, handle.kind as OptionKind).map((r) => r.strike);
}

/**
 * OptionStrat's strike slider, on this widget's own leg state: a price
 * rail above the chain with a tick per listed strike, the spot marked, and
 * one handle per leg of the current strategy. Dragging a handle snaps it
 * to the nearest strike quoted for that leg's kind and goes through the
 * same ordering repairs a chain click does (legPicker.moveLeg), so a short
 * dragged past its long pushes the long rather than crossing it. Shift-drag
 * moves every leg together by whole strikes, offsets kept (shiftLegs).
 * Arrow keys nudge a focused handle one strike; with Shift, all of them.
 *
 * Pointer capture on the handle, listeners on the captured element,
 * geometry read once at pointerdown -- the ticket splitter's pattern
 * (OptionsWidget.onSplitterDown), which survives the cursor leaving the
 * rail mid-drag.
 */
export function StrikeRail({ chain, longChain, strategy, legs, ctx, onMove, onShift }: StrikeRailProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const strikes = useMemo(() => chain.rows.map((r) => r.strike), [chain]);
  const lo = strikes[0];
  const hi = strikes[strikes.length - 1];
  const handles = useMemo(() => (legs ? legHandles(strategy, legs, ctx) : []), [strategy, legs, ctx]);

  if (strikes.length < 2 || !(hi > lo)) return null;

  const x = (price: number) => PAD + ((price - lo) / (hi - lo)) * (W - 2 * PAD);
  const priceAt = (clientX: number): number => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return lo;
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    // Undo the padding: the rail's usable width maps lo..hi.
    const inner = (frac * W - PAD) / (W - 2 * PAD);
    return lo + Math.min(1, Math.max(0, inner)) * (hi - lo);
  };
  const nearest = (rows: number[], price: number): number => {
    let best = rows[0];
    for (const s of rows) if (Math.abs(s - price) < Math.abs(best - price)) best = s;
    return best;
  };

  const onHandleDown = (handle: LegHandle) => (e: ReactPointerEvent<SVGGElement>) => {
    e.preventDefault();
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    const rows = railRows(chain, longChain, handle);
    if (rows.length === 0) return;
    const startIndex = rows.indexOf(handle.strike);
    let lastStrike = handle.strike;
    let lastDelta = 0;
    const move = (ev: PointerEvent) => {
      const price = priceAt(ev.clientX);
      const strike = nearest(rows, price);
      if (ev.shiftKey && startIndex >= 0) {
        const delta = rows.indexOf(strike) - startIndex;
        if (delta !== lastDelta) {
          onShift(delta - lastDelta);
          lastDelta = delta;
        }
        return;
      }
      if (strike !== lastStrike) {
        onMove(handle.id, strike);
        lastStrike = strike;
      }
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  };

  const onHandleKey = (handle: LegHandle) => (e: KeyboardEvent<SVGGElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    if (e.shiftKey) {
      onShift(dir);
      return;
    }
    const rows = railRows(chain, longChain, handle);
    const i = rows.indexOf(handle.strike);
    const next = rows[i + dir];
    if (next != null) onMove(handle.id, next);
  };

  // Label only the ends and the spot: forty ticks of text would overlap.
  const spotX = x(Math.min(hi, Math.max(lo, chain.spot)));
  return (
    <svg
      ref={svgRef}
      className="strike-rail"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="group"
      aria-label="Strikes: drag a leg to move it, Shift-drag to move all"
    >
      <line className="strike-rail-base" x1={PAD} x2={W - PAD} y1={BASE_Y} y2={BASE_Y} />
      {strikes.map((s, i) => (
        <line key={s} className="strike-rail-tick" x1={x(s)} x2={x(s)} y1={BASE_Y - (i % 5 === 0 ? 6 : 3)} y2={BASE_Y} />
      ))}
      <text className="strike-rail-label" x={PAD} y={H - 2} textAnchor="start">
        {formatStrike(lo)}
      </text>
      <text className="strike-rail-label" x={W - PAD} y={H - 2} textAnchor="end">
        {formatStrike(hi)}
      </text>
      <line className="strike-rail-spot" x1={spotX} x2={spotX} y1={4} y2={BASE_Y} />
      <text className="strike-rail-label strike-rail-spot-label" x={spotX} y={H - 2} textAnchor="middle">
        {chain.spot.toFixed(2)}
      </text>
      {handles.map((h) => (
        <g
          key={h.id}
          className={`strike-rail-handle ${h.role}`}
          transform={`translate(${x(Math.min(hi, Math.max(lo, h.strike)))}, ${BASE_Y - 8})`}
          tabIndex={0}
          role="slider"
          aria-label={h.label}
          aria-valuenow={h.strike}
          aria-valuemin={lo}
          aria-valuemax={hi}
          onPointerDown={onHandleDown(h)}
          onKeyDown={onHandleKey(h)}
        >
          <title>{`${h.label} ${formatStrike(h.strike)} -- drag to move, Shift-drag to move all legs, arrows to nudge`}</title>
          <circle r={6} />
          <text y={-9} textAnchor="middle" className="strike-rail-handle-label">
            {formatStrike(h.strike)}
          </text>
        </g>
      ))}
    </svg>
  );
}
