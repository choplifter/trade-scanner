import { useEffect, useRef, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { closeSpread, previewCloseSpread, previewSpread, submitSpread } from "../../api/options";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreads } from "../../hooks/useSpreads";
import { triggerBoundsLabel, type ClosePreview, type SpreadPreview } from "../../types/options";
import type { Order, Position, TradingRejection } from "../../types/trading";
import { formatLeg, type ParsedOcc } from "../../utils/occ";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import { POSITION_STOP_COLOR, POSITION_TARGET_COLOR, type OrderLevel } from "./CandleChart";

interface ContractTicketProps {
  /** The OCC symbol on the chart. */
  symbol: string;
  contract: ParsedOcc;
  mode: TradingMode;
  lastPrice: number | null;
  /** The held position on this contract, if any (Alpaca lists option
   * positions per contract, so the equity positions list carries it). */
  position: Position | null;
  /** Working orders on this contract. */
  orders: Order[];
  /** Called after an order went out, so the positions/orders poll catches
   * up right away. */
  onSubmitted?: () => void;
  /** Active premium-trigger bounds on this contract, as chart lines. */
  onTriggerLevels?: (levels: OrderLevel[]) => void;
}

type Pending =
  | { side: "buy"; preview: SpreadPreview }
  | { side: "sell"; preview: ClosePreview; heldQty: number };

function num(value: string | number | null | undefined): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value: number): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function randomUUID(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

/** Buy or sell the contract shown on the premium chart. Buying is the
 * Options widget's long call/put path (level 2, same preview, limits and
 * confirmation); selling is the close path and only offered for what is
 * held -- there is no naked writing here. Each button fetches its preview
 * once and opens the confirm dialog with the limit prefilled at the mid. */
export function ContractTicket({
  symbol,
  contract,
  mode,
  lastPrice,
  position,
  orders,
  onSubmitted,
  onTriggerLevels,
}: ContractTicketProps) {
  const [qty, setQty] = useState("1");
  const [limit, setLimit] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [liveTyped, setLiveTyped] = useState("");
  const [rejection, setRejection] = useState<TradingRejection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<string | null>(null);
  const clientOrderIdRef = useRef<string | null>(null);
  // Premium triggers on this contract: the same store and poll the Options
  // widget uses (its own hook instance, only while a contract is on the
  // chart).
  const spreads = useSpreads(mode !== "simulation");
  const [premBelow, setPremBelow] = useState("");
  const [premAbove, setPremAbove] = useState("");
  const [armTyped, setArmTyped] = useState("");
  const [armBusy, setArmBusy] = useState(false);
  const [armError, setArmError] = useState<string | null>(null);

  useEffect(() => {
    setPending(null);
    setPlaced(null);
    setRejection(null);
    setError(null);
    setArmError(null);
  }, [symbol]);

  const heldQty = position ? Math.abs(num(position.qty) ?? 0) : 0;
  const isShort = position?.side === "short";
  const entry = position ? num(position.avg_entry_price) : null;
  const pl = position ? num(position.unrealized_pl) : null;
  const qtyNum = Math.floor(Number(qty));
  const qtyOk = Number.isFinite(qtyNum) && qtyNum > 0;
  const badge = modeBadge(mode);
  const live = mode === "live";
  const canSell = heldQty > 0 && !isShort;

  const fail = (err: unknown) => {
    if (err instanceof OrderRejectedError) setRejection(err.detail);
    else setError(err instanceof Error ? err.message : String(err));
  };

  const openBuy = async () => {
    if (!qtyOk) return;
    setBusy(true);
    setRejection(null);
    setError(null);
    setPlaced(null);
    try {
      const preview = await previewSpread({
        underlying: contract.underlying,
        strategy: contract.kind === "call" ? "long_call" : "long_put",
        expiry: contract.expiry,
        qty: qtyNum,
        long_strike: contract.strike,
      });
      clientOrderIdRef.current = randomUUID();
      setLimit(preview.spread.limit_price.toFixed(2));
      setLiveTyped("");
      setPending({ side: "buy", preview });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const openSell = async () => {
    if (!qtyOk || !canSell) return;
    const sellQty = Math.min(qtyNum, heldQty);
    setBusy(true);
    setRejection(null);
    setError(null);
    setPlaced(null);
    try {
      const preview = await previewCloseSpread({ legs: [{ symbol, qty: heldQty }], qty: sellQty });
      clientOrderIdRef.current = randomUUID();
      setLimit(preview.suggested_limit.toFixed(2));
      setLiveTyped("");
      setPending({ side: "sell", preview, heldQty });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending || !liveConfirmed(mode, liveTyped)) return;
    const limitNum = Number(limit);
    if (!Number.isFinite(limitNum) || limitNum <= 0) {
      setError("The limit must be a positive price");
      return;
    }
    setBusy(true);
    try {
      const confirmWord = live ? liveTyped.trim() : undefined;
      let orderId: string | undefined;
      if (pending.side === "buy") {
        const res = await submitSpread(
          {
            underlying: contract.underlying,
            strategy: contract.kind === "call" ? "long_call" : "long_put",
            expiry: contract.expiry,
            qty: pending.preview.spread.qty,
            long_strike: contract.strike,
            limit_price: limitNum,
            client_order_id: clientOrderIdRef.current ?? undefined,
          },
          confirmWord,
        );
        orderId = res.order?.id;
      } else {
        const res = await closeSpread(
          {
            legs: [{ symbol, qty: pending.heldQty }],
            qty: pending.preview.qty,
            limit_price: limitNum,
            client_order_id: clientOrderIdRef.current ?? undefined,
          },
          confirmWord,
        );
        orderId = res.order?.id;
      }
      setPlaced(orderId ?? "submitted");
      setPending(null);
      onSubmitted?.();
    } catch (err) {
      fail(err);
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const working = orders.filter((o) => o.symbol === symbol);
  const label = formatLeg(symbol);

  // Triggers whose legs are exactly this contract.
  const triggers = spreads.triggers.filter((t) => t.legs.length === 1 && t.legs[0].symbol === symbol);
  const activeTriggers = triggers.filter((t) => t.status === "active");
  const recentTriggers = triggers.filter((t) => t.status !== "active").slice(0, 3);
  const triggerKey = activeTriggers.map((t) => `${t.id}:${t.premium_below}:${t.premium_above}`).join("|");
  useEffect(() => {
    if (!onTriggerLevels) return;
    const levels: OrderLevel[] = [];
    for (const t of activeTriggers) {
      if (t.premium_below != null) {
        levels.push({ price: t.premium_below, side: "sell", title: "Close ≤", color: POSITION_STOP_COLOR });
      }
      if (t.premium_above != null) {
        levels.push({ price: t.premium_above, side: "sell", title: "Close ≥", color: POSITION_TARGET_COLOR });
      }
    }
    onTriggerLevels(levels);
    // triggerKey stands in for activeTriggers (rebuilt every poll tick).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerKey, onTriggerLevels]);

  const arm = async () => {
    const pb = premBelow.trim() === "" ? undefined : Number(premBelow);
    const pa = premAbove.trim() === "" ? undefined : Number(premAbove);
    if (pb === undefined && pa === undefined) {
      setArmError("Enter a premium stop (≤) and/or target (≥).");
      return;
    }
    if ([pb, pa].some((v) => v !== undefined && !(v > 0))) {
      setArmError("Prices must be positive.");
      return;
    }
    if (pb !== undefined && pa !== undefined && !(pb < pa)) {
      setArmError("The stop must be below the target.");
      return;
    }
    if (!liveConfirmed(mode, armTyped)) {
      setArmError("Type LIVE to arm a real-money trigger.");
      return;
    }
    setArmBusy(true);
    setArmError(null);
    try {
      await spreads.armTrigger(
        {
          underlying: contract.underlying,
          expiry: contract.expiry,
          legs: [{ symbol, qty: isShort ? -heldQty : heldQty }],
          qty: heldQty,
          ...(pb !== undefined ? { premium_below: pb } : {}),
          ...(pa !== undefined ? { premium_above: pa } : {}),
        },
        live ? armTyped.trim() : undefined,
      );
      setPremBelow("");
      setPremAbove("");
      setArmTyped("");
    } catch (err: unknown) {
      setArmError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setArmBusy(false);
    }
  };

  return (
    <div className={`contract-ticket${live ? " live-frame" : ""}`}>
      <div className="contract-ticket-row">
        <span className={`trading-mode-badge ${badge.className}`}>{badge.label}</span>
        <label>
          Contracts{" "}
          <input type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>
        <button
          type="button"
          className={`generate-button${live ? " live-action" : ""}`}
          disabled={!qtyOk || busy}
          onClick={() => void openBuy()}
          title={`Buy ${label} to open (limit at the mid, editable before confirming)`}
        >
          Buy
        </button>
        <button
          type="button"
          className={`generate-button${live ? " live-action" : ""}`}
          disabled={!qtyOk || busy || !canSell}
          onClick={() => void openSell()}
          title={
            canSell
              ? `Sell up to ${heldQty} held ${label} to close`
              : isShort
                ? "This contract is held short; close it from the Options widget"
                : "Nothing held on this contract -- selling to open is not offered"
          }
        >
          Sell
        </button>
        <span className="order-hint">
          {heldQty > 0
            ? `held ${isShort ? "-" : ""}${heldQty} @ ${entry != null ? entry.toFixed(2) : "—"}${
                pl != null ? ` · P&L ${pl >= 0 ? "+" : ""}${money(pl)}` : ""
              }`
            : "not held"}
          {lastPrice != null ? ` · last ${lastPrice.toFixed(2)}` : ""}
          {working.length > 0
            ? ` · ${working.map((o) => `${o.side} ${o.qty ?? "?"} @ ${o.limit_price ?? "mkt"}`).join(", ")} working`
            : ""}
        </span>
        {placed && <span className="order-hint">Order placed: {placed}</span>}
      </div>
      {rejection && (
        <p className="order-rejection">
          {rejection.message}
          {rejection.field ? ` (${rejection.field})` : ""}
        </p>
      )}
      {error && <p className="order-rejection">{error}</p>}

      {heldQty > 0 && (
        <div className="contract-ticket-row trigger-editor">
          <span title="Checked every few seconds during the regular session against the contract's mid; fires a marketable limit close">
            Close if the premium is
          </span>
          <label>
            ≤{" "}
            <input
              type="number"
              step="0.01"
              value={premBelow}
              placeholder="stop"
              onChange={(e) => setPremBelow(e.target.value)}
            />
          </label>
          <label>
            ≥{" "}
            <input
              type="number"
              step="0.01"
              value={premAbove}
              placeholder="target"
              onChange={(e) => setPremAbove(e.target.value)}
            />
          </label>
          <LiveConfirmField mode={mode} value={armTyped} onChange={setArmTyped} />
          <button
            type="button"
            className={`generate-button${live ? " live-action" : ""}`}
            disabled={armBusy || !liveConfirmed(mode, armTyped)}
            onClick={() => void arm()}
          >
            {armBusy ? "Arming…" : "Arm"}
          </button>
          {activeTriggers.map((t) => (
            <span key={t.id} className="order-hint">
              <span className="trigger-status active">ACTIVE</span> {triggerBoundsLabel(t)} · {t.qty}x{" "}
              <button type="button" className="row-action" onClick={() => void spreads.cancelTrigger(t.id)}>
                Cancel
              </button>
            </span>
          ))}
        </div>
      )}
      {armError && <p className="order-rejection">{armError}</p>}
      {recentTriggers.length > 0 && heldQty === 0 && (
        <div className="contract-ticket-row">
          {recentTriggers.map((t) => (
            <span key={t.id} className="order-hint">
              <span className={`trigger-status ${t.status}`}>{t.status.toUpperCase()}</span> {triggerBoundsLabel(t)}
              {t.fired_price != null ? ` · fired at ${t.fired_price.toFixed(2)}` : ""}
              {t.last_error ? ` · ${t.last_error}` : ""}
            </span>
          ))}
        </div>
      )}

      <Modal
        open={pending != null}
        title={pending?.side === "sell" ? "Sell to close" : "Buy to open"}
        onClose={() => setPending(null)}
      >
        {pending && (
          <div className="order-confirm">
            <p className="order-confirm-line">
              <strong>
                {pending.side === "buy" ? "Buy" : "Sell"} {pending.side === "buy" ? pending.preview.spread.qty : pending.preview.qty}{" "}
                × {label}
              </strong>
            </p>
            <p className="order-confirm-line">
              mid {(pending.side === "buy" ? pending.preview.spread.net_mid : pending.preview.net_mid).toFixed(2)}
              {(() => {
                const natural = pending.side === "buy" ? pending.preview.spread.net_natural : pending.preview.net_natural;
                return natural != null ? ` · natural ${natural.toFixed(2)}` : "";
              })()}
              {pending.side === "buy" ? ` · spot ${pending.preview.spread.spot.toFixed(2)} · ${pending.preview.spread.dte}d` : ""}
            </p>
            <label className="order-confirm-line">
              Limit per contract{" "}
              <input type="number" min={0.01} step={0.01} value={limit} onChange={(e) => setLimit(e.target.value)} />
            </label>
            {pending.side === "buy" && (
              <p className="order-confirm-line">
                Pay {money(Number(limit) || 0)} × 100 × {pending.preview.spread.qty} ={" "}
                {money((Number(limit) || 0) * 100 * pending.preview.spread.qty)} · max loss the premium · breakeven{" "}
                {pending.preview.spread.breakevens.map((b) => b.toFixed(2)).join(" / ")}
              </p>
            )}
            {pending.side === "sell" && (
              <p className="order-confirm-line">
                Receive {money(Number(limit) || 0)} × 100 × {pending.preview.qty} ={" "}
                {money((Number(limit) || 0) * 100 * pending.preview.qty)}
                {entry != null ? ` · entry ${entry.toFixed(2)}` : ""}
              </p>
            )}
            {pending.side === "buy" &&
              pending.preview.spread.warnings.map((w) => (
                <p key={w} className="order-warning">
                  {w}
                </p>
              ))}
            {pending.side === "buy" && !pending.preview.can_submit && (
              <p className="order-rejection">Submitting is switched off server-side (TRADING_ENABLED / live switch).</p>
            )}
            <p className="order-confirm-mode">{badge.confirmLine}</p>
            <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setPending(null)}>
                Cancel
              </button>
              <button
                type="button"
                className={`generate-button${live ? " live-action" : ""}`}
                disabled={busy || !liveConfirmed(mode, liveTyped) || (pending.side === "buy" && !pending.preview.can_submit)}
                onClick={() => void confirm()}
              >
                {busy ? "Submitting…" : pending.side === "buy" ? "Buy to open" : "Sell to close"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
