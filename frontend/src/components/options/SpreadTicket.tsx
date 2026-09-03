import { useEffect, useRef, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { previewSpread, submitSpread } from "../../api/options";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreadLevelsContext } from "../../context/SpreadLevelsContext";
import {
  DEBIT_STRATEGIES,
  SINGLE_LEG_STRATEGIES,
  STRATEGY_LABELS,
  optionsLevelRequired,
  type ChainResponse,
  type OptionsAccountResponse,
  type SpreadPreview,
  type SpreadTicketRequest,
  type Strategy,
} from "../../types/options";
import type { TradingRejection } from "../../types/trading";
import { formatLeg, formatStrike } from "../../utils/occ";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import { isCondor, isSingle, type Legs } from "./legPicker";

const STRATEGIES: Strategy[] = ["long_call", "long_put", "bull_call", "bear_put", "bull_put", "bear_call", "iron_condor"];
/** Widget-local hotkeys (see OptionsWidget); the outright longs have none
 * because 0-4 are taken by the equity ticket. */
const STRATEGY_HOTKEY: Partial<Record<Strategy, number>> = {
  bull_call: 5,
  bear_put: 6,
  bull_put: 7,
  bear_call: 8,
  iron_condor: 9,
};
const PREVIEW_DEBOUNCE_MS = 300;

interface SpreadTicketProps {
  symbol: string;
  expiry: string;
  chain: ChainResponse | null;
  strategy: Strategy;
  onStrategy: (strategy: Strategy) => void;
  width: number;
  onWidth: (width: number) => void;
  legs: Legs | null;
  onResetLegs: () => void;
  account: OptionsAccountResponse | null;
  mode: TradingMode;
  onSubmitted: () => void;
}

function randomUUID(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

function money(value: number): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function legsLabel(strategy: Strategy, legs: Legs): string {
  if (isSingle(legs)) return `${formatStrike(legs.strike)}${strategy === "long_call" ? "C" : "P"}`;
  if (isCondor(legs)) {
    return `${formatStrike(legs.put_long)}/${formatStrike(legs.put_short)}P · ${formatStrike(legs.call_short)}/${formatStrike(legs.call_long)}C`;
  }
  const kind = strategy === "bull_call" || strategy === "bear_call" ? "C" : "P";
  return `${formatStrike(legs.long)}/${formatStrike(legs.short)}${kind}`;
}

function ticketFor(symbol: string, strategy: Strategy, expiry: string, qty: number, legs: Legs): SpreadTicketRequest {
  const base = { underlying: symbol, strategy, expiry, qty };
  if (isSingle(legs)) return { ...base, long_strike: legs.strike };
  if (isCondor(legs)) return { ...base, ...legs };
  return { ...base, long_strike: legs.long, short_strike: legs.short };
}

/** Builds and prices the spread. Everything money-related comes from the
 * server's preview (app/options/service.py) -- the ticket never does the
 * risk math itself, so what is shown is what the ceilings gate. */
export function SpreadTicket({
  symbol,
  expiry,
  chain,
  strategy,
  onStrategy,
  width,
  onWidth,
  legs,
  onResetLegs,
  account,
  mode,
  onSubmitted,
}: SpreadTicketProps) {
  const [qty, setQty] = useState("1");
  const [limit, setLimit] = useState("");
  const [limitEdited, setLimitEdited] = useState(false);
  const [preview, setPreview] = useState<SpreadPreview | null>(null);
  const [pricing, setPricing] = useState(false);
  const [rejection, setRejection] = useState<TradingRejection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [liveTyped, setLiveTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [placed, setPlaced] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const clientOrderIdRef = useRef<string | null>(null);
  const { setLevels } = useSpreadLevelsContext();

  const direction = DEBIT_STRATEGIES.has(strategy) ? "debit" : "credit";
  const single = SINGLE_LEG_STRATEGIES.has(strategy);
  const unit = single ? "contract" : "spread";
  const qtyNum = Math.floor(Number(qty));
  const qtyOk = Number.isFinite(qtyNum) && qtyNum > 0;

  // A new symbol/expiry/strategy/legs invalidates an edited limit: the mid
  // it was based on is gone.
  useEffect(() => {
    setLimitEdited(false);
    setLimit("");
    setPlaced(null);
  }, [symbol, expiry, strategy, legs]);

  // Debounced server preview on every change.
  useEffect(() => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    if (!legs || !qtyOk) {
      setPreview(null);
      setRejection(null);
      return;
    }
    const ticket = ticketFor(symbol, strategy, expiry, qtyNum, legs);
    const limitNum = Number(limit);
    if (limitEdited && Number.isFinite(limitNum) && limitNum > 0) ticket.limit_price = limitNum;
    let cancelled = false;
    setPricing(true);
    timerRef.current = window.setTimeout(() => {
      previewSpread(ticket)
        .then((res) => {
          if (cancelled) return;
          setPreview(res);
          setRejection(null);
          setError(null);
          if (!limitEdited) setLimit(res.spread.limit_price.toFixed(2));
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setPreview(null);
          if (err instanceof OrderRejectedError) setRejection(err.detail);
          else setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setPricing(false);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
    };
  }, [symbol, expiry, strategy, legs, qtyNum, qtyOk, limit, limitEdited]);

  // The strikes on the chart, for as long as this ticket is showing them.
  useEffect(() => {
    if (!legs) {
      setLevels(null);
      return;
    }
    const strikes = isSingle(legs)
      ? [{ label: `Long ${formatStrike(legs.strike)}`, price: legs.strike, role: "long" as const }]
      : isCondor(legs)
      ? [
          { label: `Put long ${formatStrike(legs.put_long)}`, price: legs.put_long, role: "long" as const },
          { label: `Put short ${formatStrike(legs.put_short)}`, price: legs.put_short, role: "short" as const },
          { label: `Call short ${formatStrike(legs.call_short)}`, price: legs.call_short, role: "short" as const },
          { label: `Call long ${formatStrike(legs.call_long)}`, price: legs.call_long, role: "long" as const },
        ]
      : [
          { label: `Long ${formatStrike(legs.long)}`, price: legs.long, role: "long" as const },
          { label: `Short ${formatStrike(legs.short)}`, price: legs.short, role: "short" as const },
        ];
    setLevels({ symbol, strikes, closeBelow: null, closeAbove: null });
  }, [symbol, legs, setLevels]);
  useEffect(() => () => setLevels(null), [setLevels]);

  const spread = preview?.spread ?? null;
  const canSubmit = Boolean(spread && preview?.can_submit) && !submitting && !pricing;
  const badge = modeBadge(mode);

  const openConfirm = () => {
    clientOrderIdRef.current = randomUUID();
    setLiveTyped("");
    setPlaced(null);
    setConfirming(true);
  };

  const doSubmit = async () => {
    if (!spread || !legs) return;
    if (!liveConfirmed(mode, liveTyped)) return;
    setSubmitting(true);
    try {
      const ticket = ticketFor(symbol, strategy, expiry, spread.qty, legs);
      ticket.limit_price = spread.limit_price;
      ticket.client_order_id = clientOrderIdRef.current ?? undefined;
      const result = await submitSpread(ticket, mode === "live" ? liveTyped.trim() : undefined);
      setPlaced(result.order?.id ?? "submitted");
      setRejection(null);
      setError(null);
      setConfirming(false);
      onSubmitted();
    } catch (err: unknown) {
      if (err instanceof OrderRejectedError) setRejection(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  };

  const level = account?.options_trading_level ?? account?.options_approved_level ?? null;
  const levelNeeded = optionsLevelRequired(strategy);
  const levelWarning =
    level != null && level < levelNeeded
      ? `This account has options level ${level}; ${STRATEGY_LABELS[strategy].toLowerCase()} needs level ${levelNeeded}.`
      : null;

  return (
    <div className="spread-ticket">
      <div className="timeframe-selector spread-strategies">
        {STRATEGIES.map((s) => (
          <button
            key={s}
            type="button"
            className="timeframe-button"
            aria-pressed={strategy === s}
            onClick={() => onStrategy(s)}
            title={
              mode === "live" || STRATEGY_HOTKEY[s] == null
                ? STRATEGY_LABELS[s]
                : `${STRATEGY_LABELS[s]} (hotkey ${STRATEGY_HOTKEY[s]})`
            }
          >
            {STRATEGY_LABELS[s]}
          </button>
        ))}
      </div>
      <div className="order-ticket-row spread-legs-row">
        <span className="order-ticket-symbol">
          {symbol} {expiry}
          {legs ? ` · ${legsLabel(strategy, legs)}` : single ? " · pick a strike in the chain" : " · pick strikes in the chain"}
        </span>
        {!single && (
          <label>
            Width{" "}
            <input
              type="number"
              min={1}
              max={20}
              step={1}
              value={width}
              onChange={(e) => onWidth(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
              title="Strikes between the long and the short leg for the default pick"
            />
          </label>
        )}
        <button type="button" className="row-action" onClick={onResetLegs} disabled={!chain}>
          Auto-pick
        </button>
      </div>
      <div className="order-ticket-row">
        <label>
          {single ? "Contracts" : "Spreads"}{" "}
          <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <label>
          {single ? "Max premium" : direction === "debit" ? "Max debit" : "Min credit"}{" "}
          <input
            type="number"
            min={0.01}
            step={0.01}
            value={limit}
            placeholder={spread ? spread.net_mid.toFixed(2) : "mid"}
            onChange={(e) => {
              setLimit(e.target.value);
              setLimitEdited(true);
            }}
          />
        </label>
        {limitEdited ? (
          <button
            type="button"
            className="row-action"
            onClick={() => {
              setLimitEdited(false);
              setLimit(spread ? spread.net_mid.toFixed(2) : "");
            }}
          >
            Back to mid
          </button>
        ) : (
          <span className="order-hint">mid</span>
        )}
      </div>

      {levelWarning && <p className="order-rejection">{levelWarning}</p>}
      {rejection && (
        <p className="order-rejection">
          {rejection.message}
          {rejection.field ? ` (${rejection.field})` : ""}
        </p>
      )}
      {error && <p className="order-rejection">{error}</p>}
      {pricing && !spread && <p className="order-hint">Pricing…</p>}
      {placed && <p className="order-hint">Order placed: {placed}</p>}

      {spread && (
        <div className="order-preview spread-summary">
          <strong>
            {spread.direction === "debit" ? "Pay" : "Receive"} {money(spread.limit_price)} × 100 × {spread.qty} ={" "}
            {money(spread.limit_price * 100 * spread.qty)}
          </strong>
          <span>
            mid {spread.net_mid.toFixed(2)}
            {spread.net_natural != null ? ` · natural ${spread.net_natural.toFixed(2)}` : ""} · spot{" "}
            {spread.spot.toFixed(2)} · {spread.dte}d
          </span>
          <span>
            Max profit {spread.max_profit == null ? "unlimited" : money(spread.max_profit)} · max loss{" "}
            {money(spread.max_loss)} · breakeven {spread.breakevens.map((b) => b.toFixed(2)).join(" / ")}
          </span>
          <span>
            {single ? "Premium" : "Collateral"} {money(spread.collateral)}
            {spread.options_buying_power != null ? ` of ${money(spread.options_buying_power)} options BP` : ""} · ceilings{" "}
            {preview!.limits.max_contracts} {single ? "contracts" : "spreads"} / {money(preview!.limits.max_order_notional)}
          </span>
          <span className="spread-legs">
            {spread.legs.map((leg) => (
              <span key={leg.symbol} className={`spread-leg ${leg.side}`}>
                {leg.side === "buy" ? "+" : "−"} {formatLeg(leg.symbol)} @ {leg.mid?.toFixed(2) ?? "—"}
                {leg.delta != null ? ` Δ${leg.delta.toFixed(2)}` : ""}
              </span>
            ))}
          </span>
          {spread.warnings.map((w) => (
            <p key={w} className="order-warning">
              {w}
            </p>
          ))}
        </div>
      )}

      <button
        type="button"
        className={`generate-button${mode === "live" ? " live-action" : ""}`}
        disabled={!canSubmit}
        onClick={openConfirm}
        title={!preview?.can_submit && spread ? "Submitting is switched off server-side (TRADING_ENABLED / live switch)" : undefined}
      >
        {spread
          ? single
            ? `Buy ${strategy === "long_call" ? "call" : "put"} on ${symbol}`
            : `${spread.direction === "debit" ? "Buy" : "Sell"} ${STRATEGY_LABELS[strategy].toLowerCase()} on ${symbol}`
          : single
            ? "Buy option"
            : "Place spread"}
      </button>

      <Modal open={confirming} title={single ? "Confirm order" : "Confirm spread"} onClose={() => setConfirming(false)}>
        {spread && (
          <div className="order-confirm">
            <p className="order-confirm-line">
              <strong>
                {STRATEGY_LABELS[strategy]} {symbol} {expiry} × {spread.qty}
              </strong>{" "}
              {spread.direction} {money(spread.limit_price)} per {unit} (limit {spread.alpaca_limit_price.toFixed(2)}, day)
            </p>
            <ul className="spread-legs">
              {spread.legs.map((leg) => (
                <li key={leg.symbol}>
                  {leg.side === "buy" ? "Buy" : "Sell"} {formatLeg(leg.symbol)} · mid {leg.mid?.toFixed(2) ?? "—"}
                </li>
              ))}
            </ul>
            <p className="order-confirm-line">
              Max profit {spread.max_profit == null ? "unlimited" : money(spread.max_profit)} · max loss{" "}
              {money(spread.max_loss)} · {single ? "premium" : "collateral"} {money(spread.collateral)}
            </p>
            {spread.warnings.map((w) => (
              <p key={w} className="order-warning">
                {w}
              </p>
            ))}
            <p className="order-confirm-mode">{badge.confirmLine}</p>
            <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button
                type="button"
                className={`generate-button${mode === "live" ? " live-action" : ""}`}
                disabled={submitting || !liveConfirmed(mode, liveTyped)}
                onClick={() => void doSubmit()}
              >
                {submitting ? "Submitting…" : single ? "Buy option" : "Place spread"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
