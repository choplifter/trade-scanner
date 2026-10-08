import { useEffect, useState } from "react";

import { OrderRejectedError } from "../../api/http";
import { cancelOptionOrder, getOptionOrders, getSpreadPayoff, previewCloseSpread } from "../../api/options";
import { liveConfirmed, modeBadge, type TradingMode } from "../../api/tradingMode";
import { useSpreadLevelsContext } from "../../context/SpreadLevelsContext";
import { useReplaySession } from "../../hooks/useReplaySession";
import {
  STRATEGY_LABELS,
  type Payoff,
  triggerBoundsLabel,
  type ClosePreview,
  type CloseSpreadRequest,
  type OptionOrderType,
  type OptionsAccountResponse,
  type OptionKind,
  type SpreadGroup,
  type SpreadTotals,
  type TriggerCreateRequest,
  type UnderlyingTrigger,
} from "../../types/options";
import type { WorkingClose } from "../../types/trading";
import { formatExpiry, formatLeg, formatStrike } from "../../utils/occ";
import { formatMoney } from "../../utils/format";
import { Modal } from "../common/Modal";
import { LiveConfirmField } from "../trading/LiveConfirmField";
import { NATENBERG, withBook } from "./bookRefs";
import { heldFigures, KeyFigures } from "./KeyFigures";
import { PayoffChart } from "./PayoffChart";
import { GreeksLine, MarginBar } from "./PositionRisk";
import { rollableLeg, type RollTarget } from "./RollTicket";
import { OrderTypeToggle } from "./SpreadTicket";
import { packageDragProps, symbolDragProps } from "../../utils/dragSymbol";

interface OpenSpreadsProps {
  spreads: SpreadGroup[];
  triggers: UnderlyingTrigger[];
  /** Greeks and collateral over the whole book, for the margin bar. */
  totals: SpreadTotals | null;
  account: OptionsAccountResponse | null;
  mode: TradingMode;
  symbol: string | null;
  loading: boolean;
  error: string | null;
  onClose: (req: CloseSpreadRequest, confirm?: string) => Promise<unknown>;
  onArm: (req: TriggerCreateRequest, confirm?: string) => Promise<unknown>;
  onCancelTrigger: (id: string) => Promise<void>;
  onSelectSymbol?: (symbol: string) => void;
  /** Opens the roll ticket on a group with a single short leg. */
  onRoll?: (group: SpreadGroup, preset?: Omit<RollTarget, "group">) => void;
}

interface PendingClose {
  group: SpreadGroup;
  preview: ClosePreview | null;
  qty: string;
  limit: string;
  orderType: OptionOrderType;
  error: string | null;
  busy: boolean;
  /** Resting closes on these legs; a new close is refused until they go. */
  working: WorkingClose[];
}

/** What a held structure's warnings are about, for the row's badge. */
function riskLabel(warnings: string[]): string {
  const pin = warnings.some((w) => w.startsWith("Pin risk"));
  const early = warnings.some((w) => !w.startsWith("Pin risk"));
  return pin && early ? "pin · assignment" : pin ? "pin" : "assignment";
}

function rejectionMessage(err: unknown): string {
  return err instanceof OrderRejectedError ? err.detail.message : err instanceof Error ? err.message : String(err);
}

/** Alpaca answers a cancel with pending_cancel and frees the contracts only
 * once it is canceled, so the replacing close has to wait for the orders to
 * leave the open list -- placed at once, it meets the same refusal. */
async function waitUntilGone(ids: string[], timeoutMs = 10_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { orders } = await getOptionOrders("open");
    if (!orders.some((o) => ids.includes(o.id))) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function workingLabel(work: WorkingClose): string {
  const price = work.limit_price != null ? `limit ${work.limit_price.toFixed(2)}` : "market";
  return `${work.contracts} × ${work.symbols.map(formatLeg).join(" / ")} · ${price}`;
}

const money = formatMoney;

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${money(value)}`;
}

/** "83% +$2.0k · 17% −$27k", the gain side green and the loss side red. */
function HeldOdds({ outlook }: { outlook: NonNullable<SpreadGroup["outlook"]> }) {
  const [gain, loss] = heldFigures(outlook, null, true)[0].value.split(" · ");
  return (
    <>
      <span className="delta-up">{gain}</span> · <span className="delta-down">{loss}</span>
    </>
  );
}

function toneClass(tone: "good" | "bad" | "neutral" | undefined): string | undefined {
  return tone === "good" ? "delta-up" : tone === "bad" ? "delta-down" : undefined;
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

/** The risk chart of a held position, fetched when its row is expanded
 * and refreshed with the poll (the legs' quotes move). */
function GroupPayoff({ group }: { group: SpreadGroup }) {
  const [payoff, setPayoff] = useState<Payoff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const legsKey = group.legs.map((leg) => `${leg.symbol}:${leg.qty}`).join("|");
  // A replay tick moves the legs' prices: the curve follows the clock.
  const replayAsOf = useReplaySession()?.as_of ?? null;
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = () =>
      getSpreadPayoff({ legs: closeLegs(group), qty: group.qty || 1, net_entry: group.net_entry })
        .then((res) => {
          if (!cancelled) {
            setPayoff(res);
            setError(null);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        });
    load();
    const id = window.setInterval(load, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // legsKey stands in for group.legs (a new array every poll tick).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legsKey, group.qty, group.net_entry, open, replayAsOf]);
  return (
    <div className="spread-risk">
      <button type="button" className="row-action" onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }} aria-expanded={open}>
        Risk {open ? "▾" : "▸"}
      </button>
      {open && error && <p className="order-rejection">{error}</p>}
      {open && payoff?.outlook && (
        <KeyFigures figures={heldFigures(payoff.outlook, group.greeks?.theta ?? null, group.net_entry < 0)} />
      )}
      {open && payoff && (
        <PayoffChart
          payoff={payoff}
          expiryLabel={group.long_expiry ? `at short expiry ${formatExpiry(payoff.expiry)}` : "at expiry"}
        />
      )}
    </div>
  );
}

/** One side of a condor as a group of its own, for closing it alone. */
function sideGroup(group: SpreadGroup, side: OptionKind): SpreadGroup {
  const legs = group.legs.filter((leg) => leg.kind === side);
  return {
    ...group,
    id: `${group.id}:${side}`,
    strategy: side === "put" ? "bull_put" : "bear_call",
    legs,
    market_value: legs.reduce((sum, leg) => sum + leg.market_value, 0),
    unrealized_pl: legs.reduce((sum, leg) => sum + leg.unrealized_pl, 0),
  };
}

// Where an adjustment moves a short leg: away to a 16-delta strike (about
// one standard deviation), closer to a 30-delta one.
const AWAY_DELTA = 0.16;
const CLOSER_DELTA = 0.3;

/** The standard ways to manage a written structure, as prefilled roll
 * tickets: the tested side out in time (same strikes, later expiry),
 * away (same expiry, further out), out and away, the untested side of a
 * condor brought closer, or the tested side closed. Each opens the roll
 * ticket or the close dialog for review; nothing is sent from here. */
function AdjustBlock({
  group,
  onRoll,
  onCloseSide,
}: {
  group: SpreadGroup;
  onRoll?: (group: SpreadGroup, preset?: Omit<RollTarget, "group">) => void;
  onCloseSide: (side: OptionKind) => void;
}) {
  const adjust = group.adjust!;
  const shortOf = (side: OptionKind) => group.legs.find((leg) => leg.kind === side && leg.qty < 0) ?? null;
  const status = (["put", "call"] as const)
    .filter((side) => adjust.sides[side])
    .map((side) => {
      const s = adjust.sides[side]!;
      return `${side} ${s.strike}${s.delta != null ? ` Δ${s.delta.toFixed(2)}` : ""}${s.tested ? " (tested)" : ""}`;
    })
    .join(" · ");
  const tested = adjust.tested;
  const testedShort = tested ? shortOf(tested) : null;
  const other: OptionKind | null = tested === "put" ? "call" : tested === "call" ? "put" : null;
  const otherShort = other && adjust.sides[other] ? shortOf(other) : null;
  const roll = (leg: { symbol: string } | null, preset: Omit<RollTarget, "group" | "legSymbol">) => {
    if (leg && onRoll) onRoll(group, { legSymbol: leg.symbol, ...preset });
  };
  return (
    <div className="adjust-block">
      <p
        className="spread-risk-line"
        title={withBook(
          `A side counts as tested once its short leg reaches ${adjust.threshold.toFixed(2)} delta or the stock trades through it -- a common management line, not a rule. Each button opens a prefilled ticket to review; nothing is sent from here.`,
          NATENBERG.adjustments,
        )}
      >
        Short legs: {status}
        {!tested && " — no side tested"}
      </p>
      {tested && testedShort && (
        <div className="adjust-actions">
          <button type="button" className="row-action" onClick={() => roll(testedShort, { presetStrike: testedShort.strike })}
            title="Same strikes, the next expiry: more time for the stock to come back, usually for a credit. The risk stays where it is.">
            Roll {tested} side out
          </button>
          <button type="button" className="row-action" onClick={() => roll(testedShort, { presetExpiry: group.expiry, presetDelta: AWAY_DELTA })}
            title="Same expiry, the short leg moved out to about 16 delta: less delta against you, usually for a debit, and a narrower profit zone.">
            Roll {tested} side away
          </button>
          <button type="button" className="row-action" onClick={() => roll(testedShort, { presetDelta: AWAY_DELTA })}
            title="The next expiry and about 16 delta at once: the time pays for the distance.">
            Out &amp; away
          </button>
          {otherShort && other && (
            <button type="button" className="row-action" onClick={() => roll(otherShort, { presetExpiry: group.expiry, presetDelta: CLOSER_DELTA })}
              title="The untested side rolled in to about 30 delta: more credit and a more neutral delta -- and risk on both sides if the stock turns back.">
              Bring {other} side closer
            </button>
          )}
          <button type="button" className="row-action" onClick={() => onCloseSide(tested)}
            title="Buy the tested side back and keep the other: the loss on that side is taken, the rest runs on.">
            Close {tested} side
          </button>
        </div>
      )}
    </div>
  );
}

/** The triggers armed on this spread: every leg a trigger closes is one of
 * the spread's own. Underlying and expiry alone are not enough -- a bear
 * put and an iron condor on the same stock and expiry are two positions,
 * and a trigger closes only the legs it was armed with (backend
 * options/monitor.py). */
export function triggersFor(group: SpreadGroup, triggers: UnderlyingTrigger[]): UnderlyingTrigger[] {
  const own = new Set(group.legs.map((leg) => leg.symbol));
  return triggers.filter(
    (t) => t.underlying === group.underlying && t.legs.length > 0 && t.legs.every((leg) => own.has(leg.symbol)),
  );
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
  totals,
  account,
  mode,
  symbol,
  loading,
  error,
  onClose,
  onArm,
  onCancelTrigger,
  onSelectSymbol,
  onRoll,
}: OpenSpreadsProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingClose | null>(null);
  const [liveTyped, setLiveTyped] = useState("");
  const [below, setBelow] = useState("");
  const [above, setAbove] = useState("");
  const [premBelow, setPremBelow] = useState("");
  const [premAbove, setPremAbove] = useState("");
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
    // Several spreads on the symbol: the one whose row is open, else the first.
    const onSymbol = spreads.filter((g) => g.underlying === symbol);
    const group = onSymbol.find((g) => g.id === expanded) ?? onSymbol[0];
    if (!group) {
      setLevels(null);
      return;
    }
    const active = triggersFor(group, triggers).find((t) => t.status === "active");
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
  }, [symbol, spreads, triggers, setLevels, expanded]);
  useEffect(() => () => setLevels(null), [setLevels]);

  const openClose = (group: SpreadGroup) => {
    setLiveTyped("");
    setPending({
      group, preview: null, qty: String(group.qty || 1), limit: "", orderType: "limit", error: null, busy: false, working: [],
    });
    previewCloseSpread({ legs: closeLegs(group), qty: group.qty || 1 })
      .then((preview) =>
        setPending((p) =>
          p && p.group.id === group.id
            ? { ...p, preview, limit: preview.suggested_limit.toFixed(2), working: preview.working_orders ?? [] }
            : p,
        ),
      )
      .catch((err: unknown) =>
        setPending((p) => (p && p.group.id === group.id ? { ...p, error: rejectionMessage(err) } : p)),
      );
  };

  const runClose = async (replace = false) => {
    if (!pending) return;
    const qty = Math.floor(Number(pending.qty));
    const limit = Number(pending.limit);
    if (!Number.isFinite(qty) || qty <= 0 || qty > (pending.group.qty || 1)) {
      setPending({ ...pending, error: `Enter a quantity between 1 and ${pending.group.qty || 1}.` });
      return;
    }
    const market = pending.orderType === "market";
    if (!market && (!Number.isFinite(limit) || limit <= 0)) {
      setPending({ ...pending, error: "Enter a positive net price." });
      return;
    }
    if (!liveConfirmed(mode, liveTyped)) return;
    setPending({ ...pending, busy: true, error: null });
    const confirm = mode === "live" ? liveTyped.trim() : undefined;
    try {
      if (replace && pending.working.length > 0) {
        const ids = pending.working.map((w) => w.id);
        for (const id of ids) await cancelOptionOrder(id, confirm);
        if (!(await waitUntilGone(ids))) {
          setPending((p) =>
            p ? { ...p, busy: false, error: "The broker has not confirmed the cancel yet; try again in a moment." } : p,
          );
          return;
        }
      }
      await onClose(
        { legs: closeLegs(pending.group), qty, ...(market ? { order_type: "market" as const } : { limit_price: limit }) },
        confirm,
      );
      setPending(null);
    } catch (err: unknown) {
      const working =
        err instanceof OrderRejectedError && err.detail.code === "close_already_working"
          ? (err.detail.working_orders ?? [])
          : null;
      setPending((p) =>
        p
          ? { ...p, busy: false, working: working ?? p.working, error: working ? null : rejectionMessage(err) }
          : p,
      );
    }
  };

  const arm = async (group: SpreadGroup) => {
    const b = below.trim() === "" ? undefined : Number(below);
    const a = above.trim() === "" ? undefined : Number(above);
    const pb = premBelow.trim() === "" ? undefined : Number(premBelow);
    const pa = premAbove.trim() === "" ? undefined : Number(premAbove);
    if (b === undefined && a === undefined && pb === undefined && pa === undefined) {
      setArmError("Enter a bound on the underlying's price and/or on the premium.");
      return;
    }
    if ([b, a, pb, pa].some((v) => v !== undefined && !(v > 0))) {
      setArmError("Prices must be positive.");
      return;
    }
    if (pb !== undefined && pa !== undefined && !(pb < pa)) {
      setArmError("The premium stop must be below the premium target.");
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
          ...(pb !== undefined ? { premium_below: pb } : {}),
          ...(pa !== undefined ? { premium_above: pa } : {}),
        },
        mode === "live" ? armTyped.trim() : undefined,
      );
      setBelow("");
      setAbove("");
      setPremBelow("");
      setPremAbove("");
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
        No option spreads held on the {account?.account === "sim" ? "simulated" : (account?.account ?? "paper")} account.
        {triggers.some((t) => t.status !== "active") ? " Recent triggers are listed once a spread is open again." : ""}
      </div>
    );
  }

  return (
    <div className="open-spreads">
      {totals && <MarginBar totals={totals} account={account} />}
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
            <th
              title={withBook(
                "The structure's theta ($ per day at a standing price) and vega ($ per point of implied volatility), from its legs' IVs. Open the row for delta, gamma and what it ties up.",
                NATENBERG.vega,
              )}
            >
              Θ / V
            </th>
            <th title="Held to expiry, against what the position is worth today: the chance of ending higher and the most it can add, the chance of ending lower and the most it can lose -- at the stock's realised-vol forecast. Hover a cell for the averages.">
              If held
            </th>
            <th title="The short legs' implied volatility against the stock's realised-vol forecast for the days left.">Vol</th>
            <th>Triggers</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {spreads.map((group) => {
            const groupTriggers = triggersFor(group, triggers);
            const active = groupTriggers.filter((t) => t.status === "active");
            const isOpen = expanded === group.id;
            const [held, vol] = group.outlook ? heldFigures(group.outlook, group.greeks?.theta ?? null, group.net_entry < 0) : [null, null];
            return [
              <tr
                key={group.id}
                aria-selected={group.underlying === symbol}
                // The package as a whole stands for its underlying, so it
                // drags onto the chart like a scanner row; ⇧-drag carries
                // its first leg's contract instead, for that leg's premium.
                // The legs themselves drag one by one when expanded.
                title="Drag onto the chart for the underlying; hold ⇧ while dragging for the first leg's premium"
                {...packageDragProps(group.underlying, group.legs[0]?.symbol ?? null)}
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
                  {group.adjust?.tested && (
                    <span
                      className="spread-broken"
                      title={`The short ${group.adjust.tested} is at ${group.adjust.sides[group.adjust.tested]?.delta?.toFixed(2) ?? "?"} delta or the stock is through it (tested from ${group.adjust.threshold.toFixed(2)}). Open the row for the adjustments.`}
                    >
                      {" "}
                      ⚠ {group.adjust.tested} side tested
                    </span>
                  )}
                  {(group.warnings?.length ?? 0) > 0 && (
                    <span className="spread-broken" title={group.warnings!.join("\n\n")}>
                      {" "}
                      ⚠ {riskLabel(group.warnings!)}
                    </span>
                  )}
                </td>
                <td>{strikesLabel(group)}</td>
                <td>{group.qty}</td>
                <td>{entryLabel(group)}</td>
                <td>{money(group.market_value)}</td>
                <td className={group.unrealized_pl >= 0 ? "delta-up" : "delta-down"}>{signed(group.unrealized_pl)}</td>
                <td className="spread-greeks-cell">
                  {group.greeks
                    ? `${group.greeks.theta >= 0 ? "+" : ""}${group.greeks.theta.toFixed(0)} / ${group.greeks.vega >= 0 ? "+" : ""}${group.greeks.vega.toFixed(0)}`
                    : "—"}
                </td>
                <td className="spread-vol-cell" title={held?.title}>
                  {group.outlook ? <HeldOdds outlook={group.outlook} /> : "—"}
                </td>
                <td className={`spread-vol-cell ${toneClass(vol?.tone) ?? ""}`} title={vol?.title}>
                  {vol ? vol.value.replace(/ %/g, "%") : "—"}
                </td>
                <td>
                  {active.length > 0
                    ? active.map((t) => (
                        <span key={t.id} className="trigger-status active" title={triggerBoundsLabel(t)}>
                          {t.close_below != null ? `≤${t.close_below}` : ""}
                          {t.close_below != null && t.close_above != null ? " " : ""}
                          {t.close_above != null ? `≥${t.close_above}` : ""}
                          {(t.close_below != null || t.close_above != null) &&
                          (t.premium_below != null || t.premium_above != null)
                            ? " "
                            : ""}
                          {t.premium_below != null ? `prem≤${t.premium_below}` : ""}
                          {t.premium_below != null && t.premium_above != null ? " " : ""}
                          {t.premium_above != null ? `prem≥${t.premium_above}` : ""}
                        </span>
                      ))
                    : "—"}
                </td>
                <td className="row-actions">
                  {onRoll && rollableLeg(group) && (
                    <button
                      type="button"
                      className="row-action"
                      title="Close a leg, or one side of the package, and open its replacement on another expiry or strike as one ticket"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRoll(group);
                      }}
                    >
                      Roll…
                    </button>
                  )}
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
                  <td colSpan={13}>
                    {(group.greeks || group.collateral) && (
                      <p className="spread-risk-line">
                        {group.greeks && <GreeksLine greeks={group.greeks} />}
                        {group.collateral ? (
                          <span title="What this structure ties up: the wider wing less the credit, a cash-secured put's strike.">
                            {group.greeks ? " · " : ""}margin {money(group.collateral)}
                          </span>
                        ) : null}
                      </p>
                    )}
                    {(group.warnings ?? []).map((w) => (
                      <p key={w} className="order-rejection" title={
                          w.startsWith("Pin risk")
                            ? withBook(w, NATENBERG.pinRisk)
                            : withBook(w, NATENBERG.earlyExerciseCalls, NATENBERG.earlyExercisePuts)
                        }>
                        {w}
                      </p>
                    ))}
                    {group.adjust && (
                      <AdjustBlock
                        group={group}
                        onRoll={onRoll}
                        onCloseSide={(side) => openClose(sideGroup(group, side))}
                      />
                    )}
                    <ul className="spread-legs">
                      {group.legs.map((leg) => (
                        <li
                          key={leg.symbol}
                          className={`spread-leg ${leg.qty > 0 ? "buy" : "sell"}`}
                          {...symbolDragProps(leg.symbol)}
                        >
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
                    <GroupPayoff group={group} />
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
                      <span title="The position's own mark: the mid of closing it, per share. Its entry is the net entry column.">
                        or its premium
                      </span>
                      <label>
                        ≤{" "}
                        <input
                          type="number"
                          step="0.01"
                          value={premBelow}
                          placeholder={group.net_entry > 0 ? "stop" : "take profit"}
                          onChange={(e) => setPremBelow(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </label>
                      <label>
                        ≥{" "}
                        <input
                          type="number"
                          step="0.01"
                          value={premAbove}
                          placeholder={group.net_entry > 0 ? "take profit" : "stop"}
                          onChange={(e) => setPremAbove(e.target.value)}
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
                            {triggerBoundsLabel(t)} · {t.qty}x
                            {t.fired_price != null
                              ? ` · fired at ${t.fired_on === "premium" ? "premium " : ""}${t.fired_price.toFixed(2)}`
                              : ""}
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
            <p className="order-confirm-line">
              <OrderTypeToggle value={pending.orderType} onChange={(orderType) => setPending({ ...pending, orderType })} />
            </p>
            {pending.orderType === "limit" ? (
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
            ) : (
              <p className="order-warning">
                Market order: closes at whatever the market gives
                {pending.preview?.net_natural != null ? ` (natural ${pending.preview.net_natural.toFixed(2)} right now)` : ""}
                {pending.group.legs.length > 1 ? "; a multi-leg package can fill well beyond it when a leg is wide" : ""}.
                Alpaca takes option market orders in the regular session only.
              </p>
            )}
            <p className="order-confirm-mode">{badge.confirmLine}</p>
            <LiveConfirmField mode={mode} value={liveTyped} onChange={setLiveTyped} />
            {pending.working.length > 0 && (
              <div className="order-warning">
                A closing order is already working on {pending.working.length === 1 ? "this position" : "these legs"}:
                <ul className="spread-legs">
                  {pending.working.map((work) => (
                    <li key={work.id}>{workingLabel(work)}</li>
                  ))}
                </ul>
                It holds the contracts, so a second close is refused. Replace cancels it, waits for the broker to
                confirm, then places this close.
              </div>
            )}
            {pending.error && <p className="order-rejection">{pending.error}</p>}
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setPending(null)}>
                Keep it
              </button>
              <button
                type="button"
                className={`generate-button${mode === "live" ? " live-action" : ""}`}
                disabled={pending.busy || !pending.preview || !liveConfirmed(mode, liveTyped)}
                onClick={() => void runClose(pending.working.length > 0)}
              >
                {pending.busy
                  ? "Working"
                  : pending.working.length > 0
                    ? "Cancel it & close"
                    : pending.orderType === "market"
                      ? "Close at market"
                      : "Close spread"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
