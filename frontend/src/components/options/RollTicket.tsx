import { useEffect, useMemo, useState } from "react";

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

/** The one leg a roll moves: a lone leg (a short put, the short call of a
 * covered call, a long call held outright), or one leg of a calendar /
 * diagonal -- the short one unless `legSymbol` names the other (a poor
 * man's wheel rolling its LEAPS out). Null for anything else (a roll of a
 * vertical is a later feature). */
export function rollableLeg(group: SpreadGroup, legSymbol?: string): SpreadPositionLeg | null {
  if (group.legs.length === 1) return group.legs[0];
  if (group.strategy === "calendar" || group.strategy === "diagonal") {
    const named = legSymbol ? group.legs.find((l) => l.symbol === legSymbol) : undefined;
    return named ?? group.legs.find((l) => l.qty < 0) ?? null;
  }
  return null;
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
  const leg = group ? rollableLeg(group, target?.legSymbol) : null;
  const long = leg != null && leg.qty > 0;
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
    if (!group || !leg) {
      setExpiries([]);
      setExpiry("");
      return;
    }
    let cancelled = false;
    // A long leg (a LEAPS) rolls out beyond the picker's window: the far
    // strip is fetched for it, from just past the strip to two years out.
    getExpiries(group.underlying, long ? { from: 61, to: 750 } : undefined)
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

  // The chain of the chosen expiry, and a default strike on it.
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
  }, [group?.id, target?.legSymbol, expiry]);

  const count = Math.max(1, Math.min(Math.floor(Number(qty)) || 1, group?.qty || 1));
  const request = useMemo<RollRequest | null>(() => {
    if (!group || !leg || !expiry || strike == null) return null;
    // A held long leg is rolled into a long leg (an outright call/put, the
    // strike-field shape); a short leg into its income shape.
    const open: RollRequest["open"] =
      leg.qty > 0
        ? { underlying: group.underlying, strategy: leg.kind === "put" ? "long_put" : "long_call", expiry, qty: count, long_strike: strike }
        : {
            underlying: group.underlying,
            strategy: leg.kind === "put" ? "cash_secured_put" : "covered_call",
            expiry,
            qty: count,
            legs: [{ kind: leg.kind, strike, side: "sell" }],
          };
    return { close: { legs: [{ symbol: leg.symbol, qty: leg.qty }], qty: count }, open };
  }, [group, leg, expiry, strike, count]);

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
      {group && !leg && <p className="order-rejection">Only a single leg, or one leg of a calendar/diagonal, can be rolled here.</p>}
      {group && leg && (
        <div className="order-confirm roll-ticket">
          <div className="roll-columns">
            <div className="roll-column">
              <p className="order-confirm-line">
                <strong>Close</strong> {formatLeg(leg.symbol)}
              </p>
              <p className="order-hint">
                {Math.abs(leg.qty)} {long ? "long" : "short"} · entry {leg.avg_entry_price.toFixed(2)} · now {leg.current_price.toFixed(2)}
                {preview ? ` · ${preview.close.direction === "debit" ? "pay" : "receive"} mid ${preview.close.net_mid.toFixed(2)}` : ""}
                {group.dte <= 0 ? " · expires today" : ` · ${group.dte}d`}
              </p>
            </div>
            <div className="roll-column">
              <p className="order-confirm-line">
                <strong>Open</strong> {long ? "buy" : "sell"} {leg.kind === "put" ? "put" : "call"}
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
                Strike{" "}
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
              {newMid != null && <p className="order-hint">new leg mid {newMid.toFixed(2)}</p>}
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
