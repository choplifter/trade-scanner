import { useEffect, useMemo, useRef, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { getChain, getExpiries, previewRoll, rollSpread } from "../../api/options";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import type { ChainResponse, ExpiryInfo, RollPreview, RollRequest, SpreadGroup, SpreadPositionLeg, StrikeRow } from "../../types/options";
import { formatMoney } from "../../utils/format";
import { formatExpiry, formatLeg, weekdayOf } from "../../utils/occ";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import { quoted } from "./legPicker";

/** What a roll starts from: the held group and the leg of it to move --
 * its single leg, or, for a calendar/diagonal, the one `legSymbol` names
 * (the short leg by default) -- and, optionally, where to roll it: a
 * playbook proposal names the expiry and strike, a click on "Roll…" leaves
 * them to the defaults. */
export interface RollTarget {
  group: SpreadGroup;
  legSymbol?: string;
  presetExpiry?: string;
  presetStrike?: number;
}

interface RollTicketProps {
  target: RollTarget | null;
  mode: TradingMode;
  onRolled: () => void;
  onClose: () => void;
}

/** What one roll moves. Either a single leg -- a short put, the short call
 * of a covered call, a long call held outright, one leg of a calendar --
 * or a vertical: two legs of the same kind and expiry, opposite signs.
 *
 * A vertical is what makes a condor rollable. The package as a whole never
 * is: there is no single replacement shape for four legs, and it is not how
 * a condor is managed anyway -- the tested side is rolled out or away, or
 * the untested one is brought closer, each on its own. */
export interface RollUnit {
  /** The risk-carrying leg's symbol; identifies the unit in a RollTarget. */
  key: string;
  label: string;
  shape: "leg" | "vertical" | "butterfly";
  /** The leg a roll is reasoned from: the short leg of a vertical, the
   * body of a butterfly, or the lone leg itself (which may be long). */
  lead: SpreadPositionLeg;
  /** The legs around it: one for a vertical, two for a butterfly, none
   * for a single leg. */
  wings: SpreadPositionLeg[];
  /** Strikes from the lead down to the lower wing, and up to the upper
   * one. A vertical fills whichever side its wing sits on; both are 0 for
   * a single leg. A butterfly with two different numbers is a broken
   * wing, and it stays broken the same way when rolled. */
  widthDown: number;
  widthUp: number;
  /** A written package (collateral held) rather than one paid for. */
  written: boolean;
}

function verticalUnit(a: SpreadPositionLeg, b: SpreadPositionLeg): RollUnit | null {
  if (a.kind !== b.kind || a.expiry !== b.expiry) return null;
  const short = a.qty < 0 ? a : b;
  const long = a.qty < 0 ? b : a;
  if (short.qty >= 0 || long.qty <= 0 || Math.abs(short.qty) !== Math.abs(long.qty)) return null;
  const written = short.kind === "put" ? short.strike > long.strike : short.strike < long.strike;
  // The leg a roll is reasoned from: the written one, or on a vertical that
  // was paid for, the long leg that carries the position.
  const lead = written ? short : long;
  const wing = written ? long : short;
  const below = wing.strike < lead.strike;
  return {
    key: lead.symbol,
    label: `${short.kind} side ${Math.min(short.strike, long.strike)}/${Math.max(short.strike, long.strike)}${short.kind === "put" ? "P" : "C"}`,
    shape: "vertical",
    lead,
    wings: [wing],
    widthDown: below ? lead.strike - wing.strike : 0,
    widthUp: below ? 0 : wing.strike - lead.strike,
    written,
  };
}

/** A butterfly: a doubled body written against two long wings, all of one
 * kind and one expiry. Rolled as a whole -- its two wings are what cap the
 * risk, so moving one alone would leave something else entirely. */
function butterflyUnit(legs: SpreadPositionLeg[]): RollUnit | null {
  if (legs.length !== 3) return null;
  const kind = legs[0].kind;
  if (legs.some((l) => l.kind !== kind || l.expiry !== legs[0].expiry)) return null;
  const body = legs.find((l) => l.qty < 0);
  const wings = legs.filter((l) => l.qty > 0).sort((a, b) => a.strike - b.strike);
  if (!body || wings.length !== 2) return null;
  // The body carries as many contracts as the wings together, and sits
  // between them.
  if (Math.abs(body.qty) !== wings[0].qty + wings[1].qty) return null;
  if (!(wings[0].strike < body.strike && body.strike < wings[1].strike)) return null;
  return {
    key: body.symbol,
    label: `${wings[0].strike}/${body.strike}/${wings[1].strike}${kind === "put" ? "P" : "C"}`,
    shape: "butterfly",
    lead: body,
    wings,
    widthDown: body.strike - wings[0].strike,
    widthUp: wings[1].strike - body.strike,
    written: false,
  };
}

/** Every unit of `group` that can be rolled, in the order they are offered. */
export function rollUnits(group: SpreadGroup): RollUnit[] {
  const single = (leg: SpreadPositionLeg): RollUnit => ({
    key: leg.symbol,
    label: `${leg.strike}${leg.kind === "put" ? "P" : "C"}`,
    shape: "leg",
    lead: leg,
    wings: [],
    widthDown: 0,
    widthUp: 0,
    written: leg.qty < 0,
  });
  if (group.legs.length === 1) return [single(group.legs[0])];
  if (group.strategy === "calendar" || group.strategy === "diagonal") {
    // Each leg on its own: the short one first (the wheel's usual roll),
    // the long one for a poor man's wheel rolling its LEAPS out.
    return [...group.legs].sort((a, b) => a.qty - b.qty).map(single);
  }
  const units: RollUnit[] = [];
  for (const kind of ["put", "call"] as const) {
    const legs = group.legs.filter((l) => l.kind === kind);
    const unit = legs.length === 2 ? verticalUnit(legs[0], legs[1]) : legs.length === 3 ? butterflyUnit(legs) : null;
    if (unit) units.push(unit);
  }
  return units;
}

/** The unit a target names, or the first one offered. */
export function rollUnitFor(group: SpreadGroup, legSymbol?: string): RollUnit | null {
  const units = rollUnits(group);
  if (units.length === 0) return null;
  if (!legSymbol) return units[0];
  return units.find((u) => u.key === legSymbol || u.wings.some((w) => w.symbol === legSymbol)) ?? units[0];
}

/** Kept for the callers that only ask whether anything can be rolled. */
export function rollableLeg(group: SpreadGroup, legSymbol?: string): SpreadPositionLeg | null {
  return rollUnitFor(group, legSymbol)?.lead ?? null;
}

type RollOpenStrategy =
  | "long_call"
  | "long_put"
  | "covered_call"
  | "cash_secured_put"
  | "bull_put"
  | "bear_call"
  | "bull_call"
  | "bear_put"
  | "call_butterfly"
  | "put_butterfly";

/** The ticket shape a rolled unit opens into. The backend holds the same
 * rule by leg count (models.ROLL_OPEN_BY_LEGS): a roll replaces a shape
 * with itself, one expiry or strike along. */
function openStrategy(unit: RollUnit): RollOpenStrategy {
  const put = unit.lead.kind === "put";
  if (unit.shape === "butterfly") return put ? "put_butterfly" : "call_butterfly";
  if (unit.shape === "leg") {
    if (unit.lead.qty > 0) return put ? "long_put" : "long_call";
    return put ? "cash_secured_put" : "covered_call";
  }
  if (unit.written) return put ? "bull_put" : "bear_call";
  return put ? "bear_put" : "bull_call";
}

const ROLL_DELTA = 0.3;
const LONG_ROLL_DELTA = 0.8;

/** The default strike for the new leg. A short leg: the same strike while
 * it is still out of the money, else the listed strike nearest 0.30 delta
 * on the new expiry's chain -- the wheel's usual re-pick. A long leg: the
 * same strike when listed, else the one nearest 0.80 delta -- a LEAPS
 * rolled out stays deep in the money. */
function defaultStrike(leg: SpreadPositionLeg, chain: ChainResponse): number | null {
  const rows = quoted(chain.rows, leg.kind);
  if (rows.length === 0) return null;
  const long = leg.qty > 0;
  const otm = leg.kind === "put" ? leg.strike < chain.spot : leg.strike > chain.spot;
  if ((long || otm) && rows.some((r) => r.strike === leg.strike)) return leg.strike;
  const wanted = long ? LONG_ROLL_DELTA : ROLL_DELTA;
  const withDelta = rows.filter((r) => (leg.kind === "put" ? r.put?.delta : r.call?.delta) != null);
  if (withDelta.length) {
    return withDelta.reduce((best, r) => {
      const d = Math.abs((leg.kind === "put" ? r.put!.delta! : r.call!.delta!) as number);
      const b = Math.abs((leg.kind === "put" ? best.put!.delta! : best.call!.delta!) as number);
      return Math.abs(d - wanted) < Math.abs(b - wanted) ? r : best;
    }).strike;
  }
  if (long) {
    // No deltas: a strike about 15 % in the money.
    const itm = leg.kind === "call" ? chain.spot * 0.85 : chain.spot * 1.15;
    return rows.reduce((best, r) => (Math.abs(r.strike - itm) < Math.abs(best.strike - itm) ? r : best)).strike;
  }
  // No deltas: the nearest OTM strike about 5 % away.
  const target = leg.kind === "put" ? chain.spot * 0.95 : chain.spot * 1.05;
  return rows.reduce((best, r) => (Math.abs(r.strike - target) < Math.abs(best.strike - target) ? r : best)).strike;
}

function errorText(err: unknown): string {
  return err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err);
}

/**
 * Close a held short leg and open its replacement as one ticket: the held
 * leg on the left, the new expiry and strike on the right, the net per
 * package underneath. In Simulation the book fills the roll as one package
 * (both legs or neither) at the natural unless a net limit is typed; at
 * Alpaca it is two orders in sequence and the result says if the second
 * was refused.
 */
export function RollTicket({ target, mode, onRolled, onClose }: RollTicketProps) {
  const group = target?.group ?? null;
  const units = useMemo(() => (group ? rollUnits(group) : []), [group]);
  const [unitKey, setUnitKey] = useState<string | null>(null);
  const unit = group ? (units.find((u) => u.key === unitKey) ?? rollUnitFor(group, target?.legSymbol)) : null;
  const leg = unit?.lead ?? null;
  const long = leg != null && leg.qty > 0;
  // The new package's wings, as distances from the lead strike: a vertical
  // uses whichever side its wing sits on, a butterfly both.
  const [widthDown, setWidthDown] = useState<number>(0);
  const [widthUp, setWidthUp] = useState<number>(0);
  // Which side the ticket is currently showing, so a switch can be told
  // apart from the first render (see the effect below).
  const shownUnit = useRef<string | null>(null);
  const [expiries, setExpiries] = useState<ExpiryInfo[]>([]);
  const [expiry, setExpiry] = useState<string>("");
  const [chain, setChain] = useState<ChainResponse | null>(null);
  const [strike, setStrike] = useState<number | null>(null);
  const [qty, setQty] = useState<string>("1");
  const [limit, setLimit] = useState<string>("");
  const [preview, setPreview] = useState<RollPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [liveTyped, setLiveTyped] = useState("");
  const badge = modeBadge(mode);

  // A new target: its expiries, the first one after the held expiry (or the
  // preset) selected, the form otherwise clean.
  useEffect(() => {
    setChain(null);
    setStrike(target?.presetStrike ?? null);
    setPreview(null);
    setError(null);
    setResult(null);
    setLimit("");
    setLiveTyped("");
    setQty(String(group?.qty || 1));
    setUnitKey(null);
    setWidthDown(unit?.widthDown ?? 0);
    setWidthUp(unit?.widthUp ?? 0);
    // A new target: whatever side it lands on counts as the first shown,
    // so the switch effect below leaves its preset strike alone.
    shownUnit.current = null;
    if (!group || !leg) {
      setExpiries([]);
      setExpiry("");
      return;
    }
    let cancelled = false;
    // A long leg (a LEAPS) rolls out beyond the picker's window: the whole
    // board is listed for it.
    getExpiries(group.underlying, { board: long })
      .then((res) => {
        if (cancelled) return;
        setExpiries(res.expiries);
        const preset = target?.presetExpiry && res.expiries.some((e) => e.expiry === target.presetExpiry) ? target.presetExpiry : null;
        const later = res.expiries.find((e) => e.expiry > group.expiry && e.dte >= 1) ?? res.expiries.find((e) => e.dte >= 1) ?? null;
        setExpiry(preset ?? later?.expiry ?? "");
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group?.id, target?.legSymbol, target?.presetExpiry, target?.presetStrike]);

  // A side switched inside the ticket starts from that side's own width.
  // Only on a real switch: on the first render the reset effect above has
  // just put a playbook's preset strike in, and this would drop it.
  useEffect(() => {
    if (!unit) {
      shownUnit.current = null;
      return;
    }
    const previous = shownUnit.current;
    shownUnit.current = unit.key;
    if (previous === null || previous === unit.key) return;
    setWidthDown(unit.widthDown);
    setWidthUp(unit.widthUp);
    setStrike(null);
    setPreview(null);
    setLimit("");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unit?.key]);

  // The chain of the chosen expiry, and a default strike on it. Keyed on
  // the unit as well: switching sides inside the ticket clears the strike,
  // and without this the select would sit on the chain's first row with no
  // request to price.
  useEffect(() => {
    if (!group || !leg || !expiry) return;
    let cancelled = false;
    getChain(group.underlying, expiry)
      .then((res) => {
        if (cancelled) return;
        setChain(res);
        setStrike((cur) => (cur != null && res.rows.some((r) => r.strike === cur) ? cur : defaultStrike(leg, res)));
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group?.id, unit?.key, expiry]);

  const count = Math.max(1, Math.min(Math.floor(Number(qty)) || 1, group?.qty || 1));
  // The wings of the new package, as strikes: the same distances from the
  // new lead strike, on the same sides as the ones held.
  const wingStrikes = useMemo<number[]>(() => {
    if (!unit || strike == null || unit.shape === "leg") return [];
    const out: number[] = [];
    if (unit.widthDown > 0) out.push(strike - widthDown);
    if (unit.widthUp > 0) out.push(strike + widthUp);
    return out;
  }, [unit, strike, widthDown, widthUp]);
  const wingsOk =
    unit != null &&
    (unit.shape === "leg" ||
      (wingStrikes.length === unit.wings.length &&
        wingStrikes.every((k) => k > 0 && chain != null && chain.rows.some((r) => r.strike === k))));

  const request = useMemo<RollRequest | null>(() => {
    if (!group || !unit || !leg || !expiry || strike == null) return null;
    const strategy = openStrategy(unit);
    let open: RollRequest["open"];
    if (unit.shape === "butterfly") {
      if (!wingsOk || wingStrikes.length !== 2) return null;
      const [low, high] = [Math.min(...wingStrikes), Math.max(...wingStrikes)];
      open = {
        underlying: group.underlying,
        strategy,
        expiry,
        qty: count,
        legs: [
          { kind: leg.kind, strike: low, side: "buy" },
          { kind: leg.kind, strike, side: "sell", ratio: 2 },
          { kind: leg.kind, strike: high, side: "buy" },
        ],
      };
    } else if (unit.shape === "vertical") {
      if (!wingsOk || wingStrikes.length !== 1) return null;
      // The written leg is `short_strike`, its wing `long_strike`,
      // whichever way round the strikes sit.
      const wing = wingStrikes[0];
      const short = unit.written ? strike : wing;
      const longStrike = unit.written ? wing : strike;
      open = { underlying: group.underlying, strategy, expiry, qty: count, short_strike: short, long_strike: longStrike };
    } else if (leg.qty > 0) {
      open = { underlying: group.underlying, strategy, expiry, qty: count, long_strike: strike };
    } else {
      open = {
        underlying: group.underlying,
        strategy,
        expiry,
        qty: count,
        legs: [{ kind: leg.kind, strike, side: "sell" }],
      };
    }
    const closeLegs = [unit.lead, ...unit.wings].map((l) => ({ symbol: l.symbol, qty: l.qty }));
    return { close: { legs: closeLegs, qty: count }, open };
  }, [group, unit, leg, expiry, strike, count, wingStrikes, wingsOk]);

  // Price the roll whenever its shape changes (debounced a little: a strike
  // scrolled through with the keyboard should not fire a preview per step).
  useEffect(() => {
    if (!request) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      previewRoll(request)
        .then((p) => {
          if (cancelled) return;
          setPreview(p);
          setError(null);
          setLimit((cur) => (cur === "" ? p.net.suggested_limit.toFixed(2) : cur));
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setPreview(null);
            setError(errorText(err));
          }
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [request]);

  const run = async () => {
    if (!request || !preview) return;
    const net = Number(limit);
    if (!Number.isFinite(net) || net <= 0) {
      setError("Enter a positive net price, or clear it to fill at the natural.");
      return;
    }
    if (!liveConfirmed(mode, liveTyped)) return;
    setBusy(true);
    setError(null);
    try {
      const res = await rollSpread(
        { ...request, limit_net: net, limit_direction: preview.net.direction },
        mode === "live" ? liveTyped.trim() : undefined,
      );
      if (res.open_error) {
        setResult(`The close was placed, the new leg was refused: ${res.open_error}`);
      } else if (res.order && res.order.status !== "filled") {
        setResult("The roll rests as one package until the market meets its net limit.");
      } else {
        setResult(null);
        onRolled();
        return;
      }
      onRolled();
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const rows: StrikeRow[] = chain && leg ? quoted(chain.rows, leg.kind) : [];
  const newMid = strike != null && chain ? (leg?.kind === "put" ? rows.find((r) => r.strike === strike)?.put?.mid : rows.find((r) => r.strike === strike)?.call?.mid) : null;

  return (
    <Modal open={target !== null} title="Roll" onClose={onClose}>
      {group && !leg && (
        <p className="order-rejection">
          Nothing here can be rolled: a single leg, one leg of a calendar or diagonal, or one side of a vertical or
          condor can, a whole package cannot.
        </p>
      )}
      {group && leg && unit && (
        <div className="order-confirm roll-ticket">
          {units.length > 1 && (
            <div className="order-confirm-line">
              <span className="order-hint">Roll </span>
              <span className="timeframe-selector">
                {units.map((u) => (
                  <button
                    key={u.key}
                    type="button"
                    className="timeframe-button"
                    aria-pressed={u.key === unit.key}
                    onClick={() => setUnitKey(u.key)}
                  >
                    {u.label}
                  </button>
                ))}
              </span>
            </div>
          )}
          <div className="roll-columns">
            <div className="roll-column">
              <p className="order-confirm-line">
                <strong>Close</strong> {formatLeg(leg.symbol)}
                {unit.wings.map((w) => ` + ${formatLeg(w.symbol)}`).join("")}
              </p>
              <p className="order-hint">
                {Math.abs(leg.qty)} {long ? "long" : "short"}
                {unit.shape === "leg"
                  ? ` · entry ${leg.avg_entry_price.toFixed(2)} · now ${leg.current_price.toFixed(2)}`
                  : ` · ${unit.shape === "butterfly" ? `${unit.widthDown}/${unit.widthUp} wings` : `${unit.widthDown || unit.widthUp} wide`} · ${
                      unit.shape === "butterfly" ? "body" : unit.written ? "short" : "long"
                    } leg ${leg.avg_entry_price.toFixed(2)} → ${leg.current_price.toFixed(2)}`}
                {preview ? ` · ${preview.close.direction === "debit" ? "pay" : "receive"} mid ${preview.close.net_mid.toFixed(2)}` : ""}
                {group.dte <= 0 ? " · expires today" : ` · ${group.dte}d`}
              </p>
            </div>
            <div className="roll-column">
              <p className="order-confirm-line">
                <strong>Open</strong>{" "}
                {unit.shape === "butterfly"
                  ? `buy ${leg.kind} butterfly`
                  : unit.shape === "vertical"
                    ? `${unit.written ? "sell" : "buy"} ${leg.kind} spread`
                    : `${long ? "buy" : "sell"} ${leg.kind}`}
              </p>
              <label className="order-confirm-line">
                Expiry{" "}
                <select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                  {expiries
                    .filter((e) => e.dte >= 1)
                    .map((e) => (
                      <option key={e.expiry} value={e.expiry}>
                        {weekdayOf(e.expiry)} {formatExpiry(e.expiry)} · {e.dte}d
                      </option>
                    ))}
                </select>
              </label>
              <label className="order-confirm-line">
                {unit.shape === "butterfly" ? "Body " : unit.shape === "vertical" ? (unit.written ? "Short strike " : "Long strike ") : "Strike "}
                <select value={strike ?? ""} onChange={(e) => setStrike(Number(e.target.value))} disabled={rows.length === 0}>
                  {rows.map((r) => {
                    const q = leg.kind === "put" ? r.put : r.call;
                    return (
                      <option key={r.strike} value={r.strike}>
                        {r.strike}
                        {q?.mid != null ? ` · ${q.mid.toFixed(2)}` : ""}
                        {q?.delta != null ? ` · Δ ${q.delta.toFixed(2)}` : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
              {unit.shape !== "leg" && (
                <label className="order-confirm-line">
                  {unit.shape === "butterfly" ? "Wings " : "Width "}
                  {unit.widthDown > 0 && (
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={widthDown}
                      title={unit.shape === "butterfly" ? "Strikes from the body down to the lower wing" : "Strikes between the legs"}
                      onChange={(e) => setWidthDown(Math.max(0, Number(e.target.value) || 0))}
                    />
                  )}
                  {unit.widthUp > 0 && (
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={widthUp}
                      title={unit.shape === "butterfly" ? "Strikes from the body up to the upper wing" : "Strikes between the legs"}
                      onChange={(e) => setWidthUp(Math.max(0, Number(e.target.value) || 0))}
                    />
                  )}
                  {wingStrikes.length > 0 && (
                    <span className="order-hint">
                      {" "}
                      {wingStrikes.length > 1 ? "wings" : "wing"} {wingStrikes.join(" / ")}
                      {leg.kind === "put" ? "P" : "C"}
                      {wingsOk ? "" : " — not listed on this expiry"}
                    </span>
                  )}
                </label>
              )}
              {unit.shape === "leg" && newMid != null && <p className="order-hint">new leg mid {newMid.toFixed(2)}</p>}
            </div>
          </div>
          {preview ? (
            <p className="order-confirm-line">
              Net {preview.net.direction === "credit" ? "credit" : "debit"} mid {preview.net.mid.toFixed(2)}
              {preview.net.natural != null ? ` · natural ${preview.net.natural.toFixed(2)}` : ""} per package
              {preview.collateral_delta !== 0 ? ` · collateral ${preview.collateral_delta > 0 ? "+" : ""}${formatMoney(preview.collateral_delta)}` : ""}
            </p>
          ) : (
            <p className="order-hint">{error ? "" : "Pricing…"}</p>
          )}
          {preview?.warnings.map((w) => (
            <p key={w} className="idea-warning">
              {w}
            </p>
          ))}
          <label className="order-confirm-line">
            Packages{" "}
            <input type="number" min={1} max={group.qty || 1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
          <label className="order-confirm-line">
            Net limit{" "}
            <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
            {preview ? <span className="order-hint"> {preview.net.direction === "credit" ? "at least this credit" : "at most this debit"}</span> : null}
          </label>
          <p className="order-confirm-mode">
            {badge.confirmLine}
            {mode === "simulation" ? " One package: both legs fill or neither." : " Two orders at Alpaca: the close, then the open."}
          </p>
          <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
          {error && <p className="order-rejection">{error}</p>}
          {result && <p className="idea-warning">{result}</p>}
          <div className="order-confirm-actions">
            <button type="button" className="timeframe-button" onClick={onClose}>
              Keep it
            </button>
            <button
              type="button"
              className={`generate-button${mode === "live" ? " live-action" : ""}`}
              disabled={busy || !preview || !preview.can_submit || !liveConfirmed(mode, liveTyped)}
              onClick={() => void run()}
            >
              {busy ? "Working" : "Roll"}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
