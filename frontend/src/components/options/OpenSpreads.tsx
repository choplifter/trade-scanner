import { useEffect, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { previewCloseSpread } from "../../api/options";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreadLevelsContext } from "../../context/SpreadLevelsContext";
import {
  STRATEGY_LABELS,
  type ClosePreview,
  type CloseSpreadRequest,
  type OptionsAccountResponse,
  type SpreadGroup,
  type TriggerCreateRequest,
  type UnderlyingTrigger,
} from "../../types/options";
import { formatExpiry, formatLeg, formatStrike } from "../../utils/occ";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";

interface OpenSpreadsProps {
  spreads: SpreadGroup[];
  triggers: UnderlyingTrigger[];
  account: OptionsAccountResponse | null;
  mode: TradingMode;
  symbol: string | null;
  loading: boolean;
  error: string | null;
  onClose: (req: CloseSpreadRequest, confirm?: string) => Promise<unknown>;
  onArm: (req: TriggerCreateRequest, confirm?: string) => Promise<unknown>;
  onCancelTrigger: (id: string) => Promise<void>;
  onSelectSymbol?: (symbol: string) => void;
}

interface PendingClose {
  group: SpreadGroup;
  preview: ClosePreview | null;
  qty: string;
  limit: string;
  error: string | null;
  busy: boolean;
}

function money(value: number): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${money(value)}`;
}

function strategyLabel(group: SpreadGroup): string {
  if (group.strategy === "broken") return "broken";
  if (group.strategy === "custom") return "custom";
  return STRATEGY_LABELS[group.strategy];
}

function strikesLabel(group: SpreadGroup): string {
  return group.legs.map((leg) => `${formatStrike(leg.strike)}${leg.kind === "call" ? "C" : "P"}`).join("/");
}

function entryLabel(group: SpreadGroup): string {
  if (group.qty === 0) return "—";
  const abs = Math.abs(group.net_entry).toFixed(2);
  return group.net_entry > 0 ? `${abs} db` : `${abs} cr`;
}

function closeLegs(group: SpreadGroup) {
  return group.legs.map((leg) => ({ symbol: leg.symbol, qty: leg.qty }));
}

/** Held spreads with P&L, a close dialog, and the underlying stop/target
 * editor. Publishes the selected symbol's held strikes and armed bounds
 * to the chart while this tab is showing. */
export function OpenSpreads({
  spreads,
  triggers,
  account,
  mode,
  symbol,
  loading,
  error,
  onClose,
  onArm,
  onCancelTrigger,
  onSelectSymbol,
}: OpenSpreadsProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingClose | null>(null);
  const [liveTyped, setLiveTyped] = useState("");
  const [below, setBelow] = useState("");
  const [above, setAbove] = useState("");
  const [armTyped, setArmTyped] = useState("");
  const [armError, setArmError] = useState<string | null>(null);
  const [armBusy, setArmBusy] = useState(false);
  const { setLevels } = useSpreadLevelsContext();
  const badge = modeBadge(mode);

  // Chart lines for the selected symbol's held spread and its live bounds.
  useEffect(() => {
    if (!symbol) {
      setLevels(null);
      return;
    }
    const group = spreads.find((g) => g.underlying === symbol);
    if (!group) {
      setLevels(null);
      return;
    }
    const active = triggers.find(
      (t) => t.status === "active" && t.underlying === group.underlying && t.expiry === group.expiry,
    );
    setLevels({
      symbol,
      strikes: group.legs.map((leg) => ({
        label: `${leg.qty > 0 ? "Long" : "Short"} ${formatStrike(leg.strike)}${leg.kind === "call" ? "C" : "P"}`,
        price: leg.strike,
        role: leg.qty > 0 ? "long" : "short",
      })),
      closeBelow: active?.close_below ?? null,
      closeAbove: active?.close_above ?? null,
    });
  }, [symbol, spreads, triggers, setLevels]);
  useEffect(() => () => setLevels(null), [setLevels]);

  const openClose = (group: SpreadGroup) => {
    setLiveTyped("");
    setPending({ group, preview: null, qty: String(group.qty || 1), limit: "", error: null, busy: false });
    previewCloseSpread({ legs: closeLegs(group), qty: group.qty || 1 })
      .then((preview) =>
        setPending((p) => (p && p.group.id === group.id ? { ...p, preview, limit: preview.suggested_limit.toFixed(2) } : p)),
      )
      .catch((err: unknown) =>
        setPending((p) =>
          p && p.group.id === group.id
            ? { ...p, error: err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err) }
            : p,
        ),
      );
  };

  const runClose = async () => {
    if (!pending) return;
    const qty = Math.floor(Number(pending.qty));
    const limit = Number(pending.limit);
    if (!Number.isFinite(qty) || qty <= 0 || qty > (pending.group.qty || 1)) {
      setPending({ ...pending, error: `Enter a quantity between 1 and ${pending.group.qty || 1}.` });
      return;
    }
    if (!Number.isFinite(limit) || limit <= 0) {
      setPending({ ...pending, error: "Enter a positive net price." });
      return;
    }
    if (!liveConfirmed(mode, liveTyped)) return;
    setPending({ ...pending, busy: true, error: null });
    try {
      await onClose(
        { legs: closeLegs(pending.group), qty, limit_price: limit },
        mode === "live" ? liveTyped.trim() : undefined,
      );
      setPending(null);
    } catch (err: unknown) {
      setPending((p) =>
        p ? { ...p, busy: false, error: err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err) } : p,
      );
    }
  };

  const arm = async (group: SpreadGroup) => {
    const b = below.trim() === "" ? undefined : Number(below);
    const a = above.trim() === "" ? undefined : Number(above);
    if (b === undefined && a === undefined) {
      setArmError("Enter a close-below and/or close-above price.");
      return;
    }
    if ((b !== undefined && !(b > 0)) || (a !== undefined && !(a > 0))) {
      setArmError("Prices must be positive.");
      return;
    }
    if (!liveConfirmed(mode, armTyped)) {
      setArmError("Type LIVE to arm a real-money trigger.");
      return;
    }
    setArmBusy(true);
    setArmError(null);
    try {
      await onArm(
        {
          underlying: group.underlying,
          expiry: group.expiry,
          legs: closeLegs(group),
          qty: group.qty || 1,
          ...(b !== undefined ? { close_below: b } : {}),
          ...(a !== undefined ? { close_above: a } : {}),
        },
        mode === "live" ? armTyped.trim() : undefined,
      );
      setBelow("");
      setAbove("");
      setArmTyped("");
    } catch (err: unknown) {
      setArmError(err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err));
    } finally {
      setArmBusy(false);
    }
  };

  if (error) return <div className="widget-error">{error}</div>;
  if (loading && spreads.length === 0) return <div className="widget-empty">Loading…</div>;
  if (spreads.length === 0) {
    return (
      <div className="widget-empty">
        No option spreads held on the {account?.account ?? "paper"} account.
        {triggers.some((t) => t.status !== "active") ? " Recent triggers are listed once a spread is open again." : ""}
      </div>
    );
  }

  return (
    <div className="open-spreads">
      <table className="performance-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Expiry</th>
            <th>Strategy</th>
            <th>Strikes</th>
            <th>Qty</th>
            <th>Entry</th>
            <th>Value</th>
            <th>P&amp;L</th>
            <th>Triggers</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {spreads.map((group) => {
            const groupTriggers = triggers.filter((t) => t.underlying === group.underlying && t.expiry === group.expiry);
            const active = groupTriggers.filter((t) => t.status === "active");
            const isOpen = expanded === group.id;
            return [
              <tr
                key={group.id}
                aria-selected={group.underlying === symbol}
                onClick={() => {
                  setExpanded(isOpen ? null : group.id);
                  setArmError(null);
                  onSelectSymbol?.(group.underlying);
                }}
              >
                <td className="symbol-cell">{group.underlying}</td>
                <td>
                  {formatExpiry(group.expiry)} ({group.dte}d)
                  {group.dte <= 0 && <span className="spread-broken"> expires today</span>}
                </td>
                <td>
                  {strategyLabel(group)}
                  {group.broken && <span className="spread-broken"> broken</span>}
                </td>
                <td>{strikesLabel(group)}</td>
                <td>{group.qty}</td>
                <td>{entryLabel(group)}</td>
                <td>{money(group.market_value)}</td>
                <td className={group.unrealized_pl >= 0 ? "delta-up" : "delta-down"}>{signed(group.unrealized_pl)}</td>
                <td>
                  {active.length > 0
                    ? active.map((t) => (
                        <span key={t.id} className="trigger-status active">
                          {t.close_below != null ? `≤${t.close_below}` : ""}
                          {t.close_below != null && t.close_above != null ? " " : ""}
                          {t.close_above != null ? `≥${t.close_above}` : ""}
                        </span>
                      ))
                    : "—"}
                </td>
                <td className="row-actions">
                  <button
                    type="button"
                    className="row-action"
                    onClick={(e) => {
                      e.stopPropagation();
                      openClose(group);
                    }}
                  >
                    Close
                  </button>
                </td>
              </tr>,
              isOpen && (
                <tr key={`${group.id}:detail`} className="spread-expand">
                  <td colSpan={10}>
                    <ul className="spread-legs">
                      {group.legs.map((leg) => (
                        <li key={leg.symbol} className={`spread-leg ${leg.qty > 0 ? "buy" : "sell"}`}>
                          {leg.qty > 0 ? "+" : ""}
                          {leg.qty}{" "}
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
                          · entry {leg.avg_entry_price.toFixed(2)} · now{" "}
                          {leg.current_price.toFixed(2)} · {signed(leg.unrealized_pl)}
                        </li>
                      ))}
                    </ul>
                    <div className="trigger-editor">
                      <span>
                        Close the spread if <strong>{group.underlying}</strong> trades
                      </span>
                      <label>
                        below{" "}
                        <input
                          type="number"
                          step="0.01"
                          value={below}
                          placeholder="stop"
                          onChange={(e) => setBelow(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </label>
                      <label>
                        above{" "}
                        <input
                          type="number"
                          step="0.01"
                          value={above}
                          placeholder="target"
                          onChange={(e) => setAbove(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </label>
                      <LiveConfirmField mode={mode} value={armTyped} onChange={setArmTyped} />
                      <button
                        type="button"
                        className={`generate-button${mode === "live" ? " live-action" : ""}`}
                        disabled={armBusy || !liveConfirmed(mode, armTyped)}
                        onClick={(e) => {
                          e.stopPropagation();
                          void arm(group);
                        }}
                      >
                        {armBusy ? "Arming…" : "Arm"}
                      </button>
                      <span className="order-hint">
                        Checked every few seconds during the regular session; fires a marketable limit close at the
                        mid. Another login can arm its own trigger on this shared account.
                      </span>
                    </div>
                    {armError && <p className="order-rejection">{armError}</p>}
                    {groupTriggers.length > 0 && (
                      <ul className="spread-legs trigger-list">
                        {groupTriggers.map((t) => (
                          <li key={t.id}>
                            <span className={`trigger-status ${t.status}`}>{t.status.toUpperCase()}</span>{" "}
                            {t.close_below != null ? `below ${t.close_below}` : ""}
                            {t.close_below != null && t.close_above != null ? " · " : ""}
                            {t.close_above != null ? `above ${t.close_above}` : ""} · {t.qty}x
                            {t.fired_price != null ? ` · fired at ${t.fired_price.toFixed(2)}` : ""}
                            {t.fired_order_id ? ` · order ${t.fired_order_id.slice(0, 8)}` : ""}
                            {t.last_error ? ` · ${t.last_error}` : ""}
                            {t.status === "active" && (
                              <>
                                {" "}
                                <button
                                  type="button"
                                  className="row-action"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void onCancelTrigger(t.id);
                                  }}
                                >
                                  Cancel
                                </button>
                              </>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>

      <Modal open={pending !== null} title="Close spread" onClose={() => setPending(null)}>
        {pending && (
          <div className="order-confirm">
            <p className="order-confirm-line">
              <strong>
                {strategyLabel(pending.group)} {pending.group.underlying} {formatExpiry(pending.group.expiry)}{" "}
                {strikesLabel(pending.group)}
              </strong>
            </p>
            {pending.preview ? (
              <>
                <ul className="spread-legs">
                  {pending.preview.legs.map((leg) => (
                    <li key={leg.symbol}>
                      {leg.side === "buy" ? "Buy" : "Sell"} to close {formatLeg(leg.symbol)} · mid{" "}
                      {leg.mid?.toFixed(2) ?? "—"}
                    </li>
                  ))}
                </ul>
                <p className="order-confirm-line">
                  {pending.preview.direction === "credit" ? "Receive" : "Pay"} mid {pending.preview.net_mid.toFixed(2)}
                  {pending.preview.net_natural != null ? ` · natural ${pending.preview.net_natural.toFixed(2)}` : ""}
                </p>
              </>
            ) : (
              <p className="order-hint">Pricing…</p>
            )}
            <label className="order-confirm-line">
              Spreads{" "}
              <input
                type="number"
                min={1}
                max={pending.group.qty || 1}
                step={1}
                value={pending.qty}
                onChange={(e) => setPending({ ...pending, qty: e.target.value })}
              />
            </label>
            <label className="order-confirm-line">
              Net limit{" "}
              <input
                type="number"
                min={0.01}
                step={0.01}
                value={pending.limit}
                onChange={(e) => setPending({ ...pending, limit: e.target.value })}
              />
            </label>
            <p className="order-confirm-mode">{badge.confirmLine}</p>
            <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
            {pending.error && <p className="order-rejection">{pending.error}</p>}
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setPending(null)}>
                Keep it
              </button>
              <button
                type="button"
                className={`generate-button${mode === "live" ? " live-action" : ""}`}
                disabled={pending.busy || !pending.preview || !liveConfirmed(mode, liveTyped)}
                onClick={() => void runClose()}
              >
                {pending.busy ? "Working" : "Close spread"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
