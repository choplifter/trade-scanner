import { useEffect, useRef, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { previewSpread, submitSpread } from "../../api/options";
import { useReplaySession } from "../../hooks/useReplaySession";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreadLevelsContext } from "../../context/SpreadLevelsContext";
import {
  SHORT_DELTA_MAX,
  SHORT_DELTA_MIN,
  SHORT_OFFSET_MAX,
  shortTargetGroup,
  type ShortTarget,
} from "../../types/options";
import {
  BUTTERFLY_STRATEGIES,
  DEBIT_STRATEGIES,
  INCOME_STRATEGIES,
  LEGS_STRATEGIES,
  SINGLE_LEG_STRATEGIES,
  STRATEGY_GROUPS,
  STRATEGY_LABELS,
  TIME_STRATEGIES,
  optionsLevelRequired,
  type ChainResponse,
  type ExpiryInfo,
  type OptionKind,
  type OptionsAccountResponse,
  type SpreadPreview,
  type SpreadTicketRequest,
  type Strategy,
  type TicketLeg,
} from "../../types/options";
import type { TradingRejection } from "../../types/trading";
import { symbolDragProps } from "../../utils/dragSymbol";
import { formatExpiry, formatLeg, formatStrike } from "../../utils/occ";
import { formatMoney } from "../../utils/format";
import { getSettings, updateSettings } from "../../api/settings";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import {
  isButterfly,
  isCondor,
  isIronButterfly,
  isSingle,
  isStrangle,
  isTime,
  legLevels,
  type Legs,
  type PickContext,
} from "./legPicker";
import { PayoffChart } from "./PayoffChart";

/** Widget-local hotkeys (see OptionsWidget); only the original spreads
 * have one, 0-4 belong to the equity ticket. */
const STRATEGY_HOTKEY: Partial<Record<Strategy, number>> = {
  bull_call: 5,
  bear_put: 6,
  bull_put: 7,
  bear_call: 8,
  iron_condor: 9,
};
const PREVIEW_DEBOUNCE_MS = 300;
const RISK_OPEN_KEY = "options:riskChartOpen";

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
  /** Clicking a leg in the summary loads that contract's premium chart. */
  onSelectSymbol?: (symbol: string) => void;
  /** Calendar/diagonal controls: the kind traded, the later expiry (and
   * the choices), and which expiry a chain click sets. */
  ctx: PickContext;
  expiries: ExpiryInfo[];
  onTimeKind: (kind: OptionKind) => void;
  onLongExpiry: (expiry: string) => void;
  onPicking: (which: "short" | "long") => void;
  /** How far out the auto-pick puts the short leg(s) for this strategy's
   * group; undefined for shapes without a short-distance setting. */
  shortTarget?: ShortTarget;
  onShortTarget?: (target: ShortTarget) => void;
}

function randomUUID(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

const money = formatMoney;

function loadRiskOpen(): boolean {
  try {
    return localStorage.getItem(RISK_OPEN_KEY) !== "closed";
  } catch {
    return true;
  }
}

function legsLabel(strategy: Strategy, legs: Legs, ctx: PickContext): string {
  const k = (kind: OptionKind) => (kind === "call" ? "C" : "P");
  if (isCondor(legs)) {
    return `${formatStrike(legs.put_long)}/${formatStrike(legs.put_short)}P · ${formatStrike(legs.call_short)}/${formatStrike(legs.call_long)}C`;
  }
  if (isIronButterfly(legs)) return `${formatStrike(legs.put_long)}P / ${formatStrike(legs.body)} / ${formatStrike(legs.call_long)}C`;
  if (isButterfly(legs)) {
    return `${formatStrike(legs.low)}/${formatStrike(legs.mid)}×2/${formatStrike(legs.high)}${k(strategy === "call_butterfly" ? "call" : "put")}`;
  }
  if (isStrangle(legs)) return `${formatStrike(legs.put)}P / ${formatStrike(legs.call)}C`;
  if (isTime(legs)) {
    const kind = k(ctx.timeKind ?? "call");
    const strikes =
      legs.short_strike === legs.long_strike
        ? formatStrike(legs.short_strike)
        : `${formatStrike(legs.short_strike)}/${formatStrike(legs.long_strike)}`;
    return `${strikes}${kind} · long ${formatExpiry(legs.long_expiry)}`;
  }
  if (isSingle(legs)) {
    if (strategy === "long_straddle") return `${formatStrike(legs.strike)} straddle`;
    return `${formatStrike(legs.strike)}${k(strategy === "long_call" || strategy === "covered_call" ? "call" : "put")}`;
  }
  return `${formatStrike(legs.long)}/${formatStrike(legs.short)}${k(strategy === "bull_call" || strategy === "bear_call" ? "call" : "put")}`;
}

/** The request body for the backend: strike fields for the original
 * shapes, an explicit legs list for the newer ones. */
export function ticketFor(
  symbol: string,
  strategy: Strategy,
  expiry: string,
  qty: number,
  legs: Legs,
  ctx: PickContext,
): SpreadTicketRequest {
  const base = { underlying: symbol, strategy, expiry, qty };
  if (!LEGS_STRATEGIES.has(strategy)) {
    if (isSingle(legs)) return { ...base, long_strike: legs.strike };
    if (isCondor(legs)) {
      return {
        ...base,
        put_long_strike: legs.put_long,
        put_short_strike: legs.put_short,
        call_short_strike: legs.call_short,
        call_long_strike: legs.call_long,
      };
    }
    if ("long" in legs && "short" in legs) return { ...base, long_strike: legs.long, short_strike: legs.short };
    return base;
  }
  const kind = ctx.timeKind ?? "call";
  let list: TicketLeg[] = [];
  if (isSingle(legs)) {
    if (strategy === "long_straddle") {
      list = [
        { kind: "put", strike: legs.strike, side: "buy" },
        { kind: "call", strike: legs.strike, side: "buy" },
      ];
    } else if (strategy === "covered_call") list = [{ kind: "call", strike: legs.strike, side: "sell" }];
    else if (strategy === "cash_secured_put") list = [{ kind: "put", strike: legs.strike, side: "sell" }];
  } else if (isStrangle(legs)) {
    list = [
      { kind: "put", strike: legs.put, side: "buy" },
      { kind: "call", strike: legs.call, side: "buy" },
    ];
  } else if (isButterfly(legs)) {
    const k: OptionKind = strategy === "call_butterfly" ? "call" : "put";
    list = [
      { kind: k, strike: legs.low, side: "buy" },
      { kind: k, strike: legs.mid, side: "sell", ratio: 2 },
      { kind: k, strike: legs.high, side: "buy" },
    ];
  } else if (isIronButterfly(legs)) {
    list = [
      { kind: "put", strike: legs.put_long, side: "buy" },
      { kind: "put", strike: legs.body, side: "sell" },
      { kind: "call", strike: legs.body, side: "sell" },
      { kind: "call", strike: legs.call_long, side: "buy" },
    ];
  } else if (isTime(legs)) {
    list = [
      { kind, strike: legs.short_strike, side: "sell" },
      { kind, strike: legs.long_strike, side: "buy", expiry: legs.long_expiry },
    ];
  }
  return { ...base, legs: list };
}

/** Builds and prices the position. Everything money-related comes from
 * the server's preview (app/options/service.py) -- the ticket never does
 * the risk math itself, so what is shown is what the ceilings gate. */
/** The limit a ticket starts with: the mid (better price, but on paper
 * an MLEG order only fills against the natural and rests otherwise) or the
 * natural (fills at once). The setting decides; the natural falls back to
 * the mid when a leg has no quote. */
export function prefillLimit(mid: number, natural: number | null, mode = getSettings().optionsLimitMode): number {
  return mode === "natural" && natural != null ? natural : mid;
}

/** Mid | Natural: which price the tickets prefill. Persists in settings. */
export function LimitModeToggle({ onChange }: { onChange?: (mode: "mid" | "natural") => void }) {
  const [mode, setMode] = useState(getSettings().optionsLimitMode);
  const pick = (next: "mid" | "natural") => {
    updateSettings({ optionsLimitMode: next });
    setMode(next);
    onChange?.(next);
  };
  return (
    <span
      className="short-target-mode"
      role="group"
      aria-label="Limit prefill"
      title="Mid: better price, may rest unfilled on paper. Natural: bid/ask, fills at once."
    >
      <button type="button" aria-pressed={mode === "mid"} onClick={() => pick("mid")}>
        Mid
      </button>
      <button type="button" aria-pressed={mode === "natural"} onClick={() => pick("natural")}>
        Natural
      </button>
    </span>
  );
}

export function SpreadTicket({
  symbol,
  expiry,
  chain,
  strategy,
  onStrategy,
  width,
  onWidth,
  shortTarget,
  onShortTarget,
  legs,
  onResetLegs,
  account,
  mode,
  onSubmitted,
  onSelectSymbol,
  ctx,
  expiries,
  onTimeKind,
  onLongExpiry,
  onPicking,
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
  const [riskOpen, setRiskOpen] = useState(loadRiskOpen);
  const timerRef = useRef<number | null>(null);
  const clientOrderIdRef = useRef<string | null>(null);
  const { setLevels } = useSpreadLevelsContext();

  const direction = DEBIT_STRATEGIES.has(strategy) ? "debit" : "credit";
  const single = SINGLE_LEG_STRATEGIES.has(strategy);
  const income = INCOME_STRATEGIES.has(strategy);
  const time = TIME_STRATEGIES.has(strategy);
  const perContract = single || strategy === "long_straddle" || strategy === "long_strangle";
  const unit = perContract ? "contract" : "spread";
  const showWidth = !single && !time && strategy !== "long_straddle" && strategy !== "long_strangle";
  const qtyNum = Math.floor(Number(qty));
  const qtyOk = Number.isFinite(qtyNum) && qtyNum > 0;
  const legsKey = legs ? JSON.stringify(legs) : "";

  // A new symbol/expiry/strategy/legs invalidates an edited limit: the mid
  // it was based on is gone.
  useEffect(() => {
    setLimitEdited(false);
    setLimit("");
    setPlaced(null);
  }, [symbol, expiry, strategy, legsKey]);

  // Debounced server preview on every change -- and on every replay tick,
  // since the legs' prices move with the clock.
  const replayAsOf = useReplaySession()?.as_of ?? null;
  useEffect(() => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    if (!legs || !qtyOk) {
      setPreview(null);
      setRejection(null);
      return;
    }
    const ticket = ticketFor(symbol, strategy, expiry, qtyNum, legs, ctx);
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
          if (!limitEdited) setLimit(prefillLimit(res.spread.limit_price, res.spread.net_natural).toFixed(2));
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
    // legsKey stands in for `legs` (rebuilt objects with equal values);
    // ctx.timeKind for the kind a calendar trades.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, expiry, strategy, legsKey, qtyNum, qtyOk, limit, limitEdited, ctx.timeKind, replayAsOf]);

  // The strikes on the chart, for as long as this ticket is showing them.
  useEffect(() => {
    if (!legs) {
      setLevels(null);
      return;
    }
    setLevels({ symbol, strikes: legLevels(strategy, legs, ctx), closeBelow: null, closeAbove: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, strategy, legsKey, setLevels]);
  useEffect(() => () => setLevels(null), [setLevels]);

  const spread = preview?.spread ?? null;
  const covered = !spread?.coverage || spread.coverage.ok;
  const canSubmit = Boolean(spread && preview?.can_submit) && covered && !submitting && !pricing;
  const badge = modeBadge(mode);

  const toggleRisk = () => {
    setRiskOpen((v) => {
      try {
        localStorage.setItem(RISK_OPEN_KEY, v ? "closed" : "open");
      } catch {
        // Not remembered; fine.
      }
      return !v;
    });
  };

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
      const ticket = ticketFor(symbol, strategy, expiry, spread.qty, legs, ctx);
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

  const actionLabel = (() => {
    if (!spread) return income ? "Write option" : single ? "Buy option" : "Place spread";
    if (strategy === "covered_call") return `Write covered call on ${symbol}`;
    if (strategy === "cash_secured_put") return `Write cash-secured put on ${symbol}`;
    if (strategy === "long_call" || strategy === "long_put") return `Buy ${strategy === "long_call" ? "call" : "put"} on ${symbol}`;
    return `${spread.direction === "debit" ? "Buy" : "Sell"} ${STRATEGY_LABELS[strategy].toLowerCase()} on ${symbol}`;
  })();

  const longExpiryChoices = expiries.filter((e) => e.expiry > expiry);

  return (
    <div className="spread-ticket">
      <div className="spread-strategy-groups">
        {STRATEGY_GROUPS.map((group) => (
          <div key={group.label} className="spread-strategy-group">
            <span className="spread-strategy-group-label">{group.label}</span>
            <div className="timeframe-selector spread-strategies">
              {group.strategies.map((s) => (
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
          </div>
        ))}
      </div>
      <div className="order-ticket-row spread-legs-row">
        <span className="order-ticket-symbol">
          {symbol} {expiry}
          {legs ? ` · ${legsLabel(strategy, legs, ctx)}` : single ? " · pick a strike in the chain" : " · pick strikes in the chain"}
        </span>
        {showWidth && (
          <label>
            {BUTTERFLY_STRATEGIES.has(strategy) ? "Wings" : "Width"}{" "}
            <input
              type="number"
              min={1}
              max={20}
              step={1}
              value={width}
              onChange={(e) => onWidth(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
              title={
                BUTTERFLY_STRATEGIES.has(strategy)
                  ? "Strikes between the body and each wing for the default pick"
                  : "Strikes between the long and the short leg for the default pick"
              }
            />
          </label>
        )}
        {shortTarget && onShortTarget && shortTargetGroup(strategy) && (
          <label
            className="short-target"
            title={
              shortTarget.mode === "delta"
                ? "Delta the short leg(s) aim for in the auto-pick: smaller = further out of the money"
                : "Strikes from the spot for the short leg(s): 0 = the first strike outside the spot (the tightest corridor)"
            }
          >
            Short{" "}
            <span className="timeframe-selector">
              <button
                type="button"
                className="timeframe-button"
                aria-pressed={shortTarget.mode === "delta"}
                onClick={() => onShortTarget({ mode: "delta", value: shortTarget.mode === "delta" ? shortTarget.value : 0.2 })}
              >
                Δ
              </button>
              <button
                type="button"
                className="timeframe-button"
                aria-pressed={shortTarget.mode === "offset"}
                onClick={() => onShortTarget({ mode: "offset", value: shortTarget.mode === "offset" ? shortTarget.value : 1 })}
              >
                Strikes
              </button>
            </span>
            <input
              type="number"
              min={shortTarget.mode === "delta" ? SHORT_DELTA_MIN : 0}
              max={shortTarget.mode === "delta" ? SHORT_DELTA_MAX : SHORT_OFFSET_MAX}
              step={shortTarget.mode === "delta" ? 0.05 : 1}
              value={shortTarget.value}
              onChange={(e) => {
                const raw = Number(e.target.value);
                if (!Number.isFinite(raw)) return;
                onShortTarget({ mode: shortTarget.mode, value: raw });
              }}
            />
          </label>
        )}
        {legs && isCondor(legs) && (
          <span className="order-hint" title="Distance between the short put and the short call">
            corridor {formatStrike(legs.call_short - legs.put_short)}
          </span>
        )}
        {legs && isStrangle(legs) && (
          <span className="order-hint" title="Distance between the put and the call">
            corridor {formatStrike(legs.call - legs.put)}
          </span>
        )}
        <button type="button" className="row-action" onClick={onResetLegs} disabled={!chain}>
          Auto-pick
        </button>
      </div>
      {time && (
        <div className="order-ticket-row spread-time-row">
          <div className="timeframe-selector">
            {(["call", "put"] as OptionKind[]).map((kind) => (
              <button
                key={kind}
                type="button"
                className="timeframe-button"
                aria-pressed={(ctx.timeKind ?? "call") === kind}
                onClick={() => onTimeKind(kind)}
              >
                {kind === "call" ? "Calls" : "Puts"}
              </button>
            ))}
          </div>
          <label>
            Long expiry{" "}
            <select value={ctx.longExpiry ?? ""} onChange={(e) => onLongExpiry(e.target.value)}>
              {longExpiryChoices.length === 0 && <option value="">none later</option>}
              {longExpiryChoices.map((e) => (
                <option key={e.expiry} value={e.expiry}>
                  {formatExpiry(e.expiry)} ({e.dte}d)
                </option>
              ))}
            </select>
          </label>
          <span className="order-hint">chain shows</span>
          <div className="timeframe-selector">
            <button type="button" className="timeframe-button" aria-pressed={ctx.picking !== "long"} onClick={() => onPicking("short")}>
              short {formatExpiry(expiry)}
            </button>
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={ctx.picking === "long"}
              onClick={() => onPicking("long")}
              disabled={!ctx.longExpiry}
            >
              long {ctx.longExpiry ? formatExpiry(ctx.longExpiry) : "—"}
            </button>
          </div>
        </div>
      )}
      <div className="order-ticket-row">
        <label>
          {perContract ? "Contracts" : "Spreads"}{" "}
          <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <label>
          {income ? "Min premium" : single ? "Max premium" : direction === "debit" ? "Max debit" : "Min credit"}{" "}
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
        <LimitModeToggle
          onChange={(mode) => {
            // Switching the mode also re-prefills, even after a manual edit:
            // the click is the "put me at the mid / the natural" request.
            setLimitEdited(false);
            if (spread) setLimit(prefillLimit(spread.net_mid, spread.net_natural, mode).toFixed(2));
          }}
        />
        {limitEdited && (
          <button
            type="button"
            className="row-action"
            onClick={() => {
              setLimitEdited(false);
              if (spread) setLimit(prefillLimit(spread.net_mid, spread.net_natural).toFixed(2));
            }}
          >
            Reset
          </button>
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
            {spread.max_loss == null ? "unbounded" : money(spread.max_loss)}
            {spread.breakevens.length > 0 ? ` · breakeven ${spread.breakevens.map((b) => b.toFixed(2)).join(" / ")}` : ""}
          </span>
          <span>
            {income ? "Cover" : single ? "Premium" : "Collateral"}{" "}
            {spread.coverage
              ? spread.coverage.kind === "shares"
                ? `${spread.coverage.have.toLocaleString()} of ${spread.coverage.need.toLocaleString()} shares`
                : `${money(spread.coverage.have)} of ${money(spread.coverage.need)} buying power`
              : money(spread.collateral)}
            {!spread.coverage && spread.options_buying_power != null ? ` of ${money(spread.options_buying_power)} options BP` : ""}{" "}
            · ceilings {preview!.limits.max_contracts} {perContract ? "contracts" : "spreads"} /{" "}
            {money(preview!.limits.max_order_notional)}
          </span>
          {spread.coverage && !spread.coverage.ok && (
            <p className="order-rejection">
              Not covered:{" "}
              {spread.coverage.kind === "shares"
                ? `${spread.coverage.need} shares needed`
                : `${money(spread.coverage.need)} buying power needed`}
              .
            </p>
          )}
          <span className="spread-legs">
            {spread.legs.map((leg) => (
              <span key={leg.symbol} className={`spread-leg ${leg.side}`} {...symbolDragProps(leg.symbol)}>
                {leg.side === "buy" ? "+" : "−"}
                {leg.ratio_qty > 1 ? `${leg.ratio_qty}×` : ""}{" "}
                {onSelectSymbol ? (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => onSelectSymbol(leg.symbol)}
                    title={`Chart this contract's premium (${leg.symbol})`}
                  >
                    {formatLeg(leg.symbol)}
                  </button>
                ) : (
                  formatLeg(leg.symbol)
                )}{" "}
                @ {leg.mid?.toFixed(2) ?? "—"}
                {leg.delta != null ? ` Δ${leg.delta.toFixed(2)}` : ""}
              </span>
            ))}
          </span>
          {spread.warnings.map((w) => (
            <p key={w} className="order-warning">
              {w}
            </p>
          ))}
          {spread.payoff && (
            <div className="spread-risk">
              <button type="button" className="row-action" onClick={toggleRisk} aria-expanded={riskOpen}>
                Risk {riskOpen ? "▾" : "▸"}
              </button>
              {riskOpen && (
                <PayoffChart
                  payoff={spread.payoff}
                  expiryLabel={time ? `at short expiry ${formatExpiry(spread.payoff.expiry)}` : "at expiry"}
                />
              )}
            </div>
          )}
        </div>
      )}

      <button
        type="button"
        className={`generate-button${mode === "live" ? " live-action" : ""}`}
        disabled={!canSubmit}
        onClick={openConfirm}
        title={
          !preview?.can_submit && spread
            ? "Submitting is switched off server-side (TRADING_ENABLED / live switch)"
            : !covered
              ? "Not covered -- see the preview"
              : undefined
        }
      >
        {actionLabel}
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
                  {leg.side === "buy" ? "Buy" : "Sell"} {leg.ratio_qty > 1 ? `${leg.ratio_qty}× ` : ""}
                  {formatLeg(leg.symbol)} · mid {leg.mid?.toFixed(2) ?? "—"}
                </li>
              ))}
            </ul>
            <p className="order-confirm-line">
              Max profit {spread.max_profit == null ? "unlimited" : money(spread.max_profit)} · max loss{" "}
              {spread.max_loss == null ? "unbounded" : money(spread.max_loss)} ·{" "}
              {income ? "cover" : single ? "premium" : "collateral"}{" "}
              {spread.coverage ? `${spread.coverage.have.toLocaleString()} ${spread.coverage.kind}` : money(spread.collateral)}
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
                {submitting ? "Submitting…" : income ? "Write option" : single ? "Buy option" : "Place spread"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
