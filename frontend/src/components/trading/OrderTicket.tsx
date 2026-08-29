import { useEffect, useRef, useState } from "react";

import { OrderRejectedError, dayHigh, previewOrder, referencePrice, submitOrder } from "../../api/http";
import { useTradingContext } from "../../context/TradingContext";
import { Modal } from "../common/Modal";
import { exitsForPosition, num } from "../../types/trading";
import type {
  Account,
  EntryOrderType,
  Order,
  OrderPreview,
  OrderTicketRequest,
  Position,
  TradingRejection,
} from "../../types/trading";
import { formatPrice } from "../../utils/format";

function money(value: string | null | undefined): string {
  const parsed = num(value);
  return parsed === null ? "—" : formatPrice(parsed);
}

type SizingMode = "shares" | "risk";

/** Which entry price each type carries. A stop-limit has both: the trigger
 * that activates it and the limit it then goes in at. */
const NEEDS_LIMIT: ReadonlySet<EntryOrderType> = new Set(["limit", "stop_limit"]);
const NEEDS_TRIGGER: ReadonlySet<EntryOrderType> = new Set(["stop", "stop_limit"]);

const ORDER_TYPES: { type: EntryOrderType; label: string; title: string }[] = [
  { type: "market", label: "Market", title: "Fills now at the current price." },
  {
    type: "limit",
    label: "Limit",
    title:
      "Buy at this price or lower / sell at this price or higher. A buy limit ABOVE the market fills immediately -- it does not wait for price to reach it.",
  },
  {
    type: "stop",
    label: "Stop",
    title:
      "Breakout entry: rests until price trades through the trigger, then fills at market. A buy stop sits above the market, a sell stop below.",
  },
  {
    type: "stop_limit",
    label: "Stop-limit",
    title:
      "Breakout entry with a cap: rests until price trades through the trigger, then goes in as a limit order.",
  },
];

/** Debounce: the preview is a round trip that also fetches the account, so
 * firing per keystroke would be wasteful and would make the displayed size
 * lag behind the field being typed into. */
const PREVIEW_DEBOUNCE_MS = 350;

/** DAS-Trader-style instant-fire hotkeys: Q/W/E buy at market, no confirm
 * dialog; Shift+Q/W/E does the same on the sell side. Paired by index with
 * INSTANT_FIRE_RISK_PCTS. Deliberately disjoint from B/S/1-4/Enter above,
 * which stay a "build a ticket, then confirm" flow -- this is a separate,
 * faster path, not a replacement for it.
 *
 * Sized by risk, not a flat share count -- DAS's own hotkey scripts size
 * every entry off the distance to a stop and a fixed dollar risk, then
 * attach that stop as a real order the instant the entry fires. This app's
 * `risk: {stop_price, risk_pct_of_equity}` ticket field already does both
 * halves of that server-side (OrderService.preview sizes qty from it;
 * resolve_ticket also sets the resolved order's stop_loss_price to the same
 * stop, which submit() turns into a real bracket leg) -- risking a % of
 * equity instead of DAS's hardcoded fixed dollar amount, since that's what
 * this app's "By risk" sizing mode (stopPrice/riskPct below) already uses
 * everywhere else. These hotkeys read that same `stopPrice` state directly,
 * so a stop has to be typed into the ticket first -- there's no way to
 * pick a stop with one keypress, unlike DAS's chart-click.
 *
 * Safe to skip previewOrder entirely: submitOrder re-derives price/size and
 * re-checks every ceiling server-side regardless (OrderService.submit calls
 * its own preview() before placing anything), so this hits the identical
 * guards the confirm-gated path does -- it just doesn't ask first. */
const INSTANT_FIRE_KEYS = ["q", "w", "e"];
const INSTANT_FIRE_RISK_PCTS = [0.5, 1, 2];

/** DAS script #15 ("High of Day"): a breakout-entry stop order at the day's
 * high plus a cent, with a market stop-loss below the trigger.
 *
 * DAS's own default for the stop-loss is a flat $0.30 -- fine for the thin,
 * low-priced runners that script targets, but this app's universe spans
 * $2-$100 (see settings.universe_min_price/max_price), and risk_pct_of_equity
 * sizing has a hard floor a flat dollar offset can blow through at the wrong
 * price: qty = (equity * riskPct/100) / stopDistance, so
 * notional = qty * price, and notional stays under the ceiling
 * (equity * maxNotionalPct/100) only when stopDistance is at least
 * (riskPct / maxNotionalPct) of price -- 4% at this app's defaults (1% risk,
 * 25% ceiling). $0.30 is under that floor for anything pricier than ~$7.50.
 * (The same floor already applies to a manually-typed stop on the Q/W/E
 * hotkeys -- the ceiling rejects those too if the stop's too tight for the
 * price; this just makes the breakout hotkey's *default* clear it instead
 * of failing on most of the universe.) A percentage of the trigger price
 * scales correctly across that whole range where a fixed dollar amount
 * can't; 8% leaves headroom above the 4% floor even if risk_pct_of_equity
 * were doubled from its current default. */
const BREAKOUT_TRIGGER_OFFSET = 0.01;
const BREAKOUT_STOP_OFFSET_PCT = 0.08;

/** Stop/Target auto-suggestion (see the effects that use these, in the
 * component body): a quick starting point so the risk-based fields -- and
 * the instant-fire hotkeys that read them -- aren't blocked on an empty
 * form, not a claim about where either level actually belongs. Retype
 * either in one edit if they're wrong for the setup.
 *
 * SUGGESTED_STOP_PCT has to clear the same floor BREAKOUT_STOP_OFFSET_PCT's
 * comment derives -- stopDistance >= (riskPct / maxNotionalPct) of price,
 * 4% at this app's defaults -- or the very first preview the suggestion
 * produces comes back rejected past the notional ceiling. A tempting
 * "quick scalp" 1-2% stop is *below* that floor at these settings, so it's
 * not actually usable as a default here; 6% clears it with real margin --
 * 5% only cleared it by a single point, too thin against the floor itself
 * moving slightly (price/equity aren't identical between suggestion and
 * fire). SUGGESTED_RR (2:1) is DAS scripts #9/#10's own reward:risk ratio
 * and has no such constraint -- the target isn't what the ceiling checks. */
const SUGGESTED_STOP_PCT = 0.06;
const SUGGESTED_RR = 2;
/** Limit/Trigger auto-suggestion, see the effect that uses these.
 * SUGGESTED_LIMIT_OFFSET is stop-limit's cap only -- a small fixed buffer
 * beyond its own trigger (DAS's Ask+/Bid-$0.05 convention, where scale
 * doesn't matter much since it's an execution-slippage cushion, not "how
 * far below market"). SUGGESTED_LIMIT_PCT is the plain-limit case -- a
 * pullback below market for a buy, scaled by price like the Stop trigger
 * so it isn't negligible on an expensive symbol the way a fixed nickel
 * would be. */
const SUGGESTED_LIMIT_OFFSET = 0.05;
const SUGGESTED_LIMIT_PCT = 0.01;
const SUGGESTED_TRIGGER_PCT = 0.03;

/** Alpaca prices in whole cents above $1 -- round() rather than toFixed()
 * since this feeds a number field, not display text. */
function roundToCent(value: number): number {
  return Math.round(value * 100) / 100;
}

function numberOrUndefined(value: string): number | undefined {
  if (value.trim() === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** crypto.randomUUID() only exists in a secure context (HTTPS or localhost)
 * -- opening the dashboard as http://<lan-hostname>:5173 from another
 * machine leaves it undefined, which used to throw inside openConfirm and
 * silently kill the click before the confirm dialog opened. getRandomValues
 * has no such restriction, so build a v4 UUID from that instead. */
function randomUUID(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

interface OrderTicketProps {
  symbol: string | null;
  defaultRiskPct: number;
  /** For the buying-power/equity context strip and the quick-% sizing
   * buttons (#1) -- read-only here, the ticket never mutates the account. */
  account: Account | null;
  /** The position already open on `symbol`, if any -- drives the "already
   * holding N @ price" context line and the existing-exit warning (#6). */
  position: Position | null;
  /** Paired with `position` via `exitsForPosition` for the existing-exit
   * warning (#6). */
  orders: Order[];
  /** Called after a successful submit so the positions/orders tables and the
   * account line refresh immediately rather than waiting for the next poll. */
  onSubmitted: () => void;
}

/** The order ticket. Sizes and prices through the server on every edit, so
 * what is shown is what the broker would receive -- the arithmetic is never
 * duplicated client-side, where it could drift from the ceilings that
 * actually gate a submit.
 *
 * Submit is gated twice over: the button only enables when the server says
 * can_submit (TRADING_ENABLED and a paper account), and the confirmation
 * dialog stands between the button and the order. */
export function OrderTicket({
  symbol,
  defaultRiskPct,
  account,
  position,
  orders,
  onSubmitted,
}: OrderTicketProps) {
  // For the chart's indicative (draft) lines -- see the effects near the
  // bottom of the hook section below. Not via props: ChartWidget is a
  // sibling, not a parent/child of this component, so the shared context is
  // the same mechanism positions/orders already use to reach it.
  const { setIndicativeLevels } = useTradingContext();
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderType, setOrderType] = useState<EntryOrderType>("market");
  const [sizingMode, setSizingMode] = useState<SizingMode>("risk");

  const [qty, setQty] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  // The entry trigger of a stop / stop-limit order. Kept apart from
  // stopPrice below, which is the *protective* stop the risk sizing works
  // from -- a breakout ticket has both, and they mean opposite things.
  const [triggerPrice, setTriggerPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [riskPct, setRiskPct] = useState(String(defaultRiskPct));
  const [takeProfit, setTakeProfit] = useState("");
  // null means "whatever the server derives from the ticket" -- a protected
  // ticket defaults to gtc so its legs outlive the close. Clicking either
  // button pins the choice instead.
  const [timeInForce, setTimeInForce] = useState<"day" | "gtc" | null>(null);

  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [previewPending, setPreviewPending] = useState(false);
  const [rejection, setRejection] = useState<TradingRejection | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [placed, setPlaced] = useState<string | null>(null);
  // Guards the instant-fire hotkeys below against a double-submit from key
  // repeat -- separate from `submitting`, which only covers the confirm-
  // dialog's own submit button.
  const [firing, setFiring] = useState(false);

  // Advisory only -- doesn't affect canSubmit/disabledReason. Reset below
  // whenever the symbol changes, so switching to a different already-exited
  // position re-shows it rather than staying dismissed from a prior symbol.
  const [existingExitsDismissed, setExistingExitsDismissed] = useState(false);
  // Whether the user has typed into Stop/Target/Limit/Trigger themselves --
  // once true, the auto-suggestion effects below leave that field alone.
  // Reset on symbol change along with existingExitsDismissed, for the same
  // reason.
  const [stopManuallyEdited, setStopManuallyEdited] = useState(false);
  const [takeProfitManuallyEdited, setTakeProfitManuallyEdited] = useState(false);
  const [limitManuallyEdited, setLimitManuallyEdited] = useState(false);
  const [triggerManuallyEdited, setTriggerManuallyEdited] = useState(false);
  useEffect(() => {
    setExistingExitsDismissed(false);
    setStopManuallyEdited(false);
    setTakeProfitManuallyEdited(false);
    setLimitManuallyEdited(false);
    setTriggerManuallyEdited(false);
  }, [symbol]);

  // Reference price for the suggestions below. Prefers the ticket's own
  // preview once one exists (freshest, and what the order will actually be
  // priced at) or the position's last known price, but neither is available
  // for a flat symbol with no stop typed yet -- previewOrder is gated on
  // the ticket being complete, which for risk sizing means a stop already
  // existing, exactly what's being bootstrapped here. fetchedReferencePrice
  // (below) breaks that circularity with an independent quote fetch.
  const [fetchedReferencePrice, setFetchedReferencePrice] = useState<number | null>(null);
  useEffect(() => {
    setFetchedReferencePrice(null);
    if (!symbol) return;
    let cancelled = false;
    referencePrice(symbol).then((price) => {
      if (!cancelled) setFetchedReferencePrice(price);
    });
    return () => {
      cancelled = true;
    };
  }, [symbol]);
  // preview.order.entry_reference only means "current market price" for a
  // market order -- resolve_ticket prices limit/stop/stop_limit tickets at
  // their own typed limit/trigger instead (see that function's own
  // comment), so for those order types entry_reference is whatever the
  // suggestion effects below just wrote. Using it there would feed the
  // suggestion back into its own input: trigger gets suggested off it,
  // the next preview echoes the trigger back as entry_reference, trigger
  // gets re-suggested further out, compounding every cycle. Restricting the
  // preview-derived value to "market" keeps it fresh where it's safe and
  // falls back to the independent fetch everywhere else.
  const suggestionReference =
    (orderType === "market" ? preview?.order?.entry_reference : undefined) ??
    num(position?.current_price) ??
    fetchedReferencePrice;

  // Stop: SUGGESTED_STOP_PCT below/above the reference price -- a quick,
  // clearly-a-placeholder starting point (round % off the current price),
  // not a real technical level. Its only job is to stop the instant-fire
  // hotkeys from being permanently disabled on an empty field; retype it for
  // anything that actually matters.
  useEffect(() => {
    if (stopManuallyEdited || suggestionReference === null) return;
    const suggested =
      side === "sell"
        ? suggestionReference * (1 + SUGGESTED_STOP_PCT)
        : suggestionReference * (1 - SUGGESTED_STOP_PCT);
    setStopPrice(roundToCent(suggested).toFixed(2));
  }, [suggestionReference, side, stopManuallyEdited]);

  // Target: SUGGESTED_RR times the *actual* stop distance (whichever value
  // is in the Stop field right now, suggested or typed), not a second
  // independent percentage -- reward:risk is a ratio to the risk actually
  // being taken, so this recomputes whenever stopPrice changes, same as it
  // would if the user retyped the stop by hand.
  useEffect(() => {
    if (takeProfitManuallyEdited || suggestionReference === null) return;
    const stopValue = numberOrUndefined(stopPrice);
    if (stopValue === undefined) return;
    const stopDistance = Math.abs(suggestionReference - stopValue);
    if (stopDistance <= 0) return;
    const suggested =
      side === "sell"
        ? suggestionReference - SUGGESTED_RR * stopDistance
        : suggestionReference + SUGGESTED_RR * stopDistance;
    if (suggested <= 0) return;
    setTakeProfit(roundToCent(suggested).toFixed(2));
  }, [suggestionReference, side, stopPrice, takeProfitManuallyEdited]);

  // Limit/Trigger: only relevant once the order type actually needs them
  // (NEEDS_LIMIT/NEEDS_TRIGGER, same sets ORDER_TYPES/the JSX below use), so
  // this fires on orderType changes -- pressing 1-4 or clicking a type
  // button -- not just once on mount.
  //
  // Trigger gets a breakout-style percentage off the market price, since
  // "Stop" here means a breakout entry (see ORDER_TYPES' own title text) --
  // a generic starting point, not the T hotkey's day-high-specific version.
  //
  // Limit is anchored differently depending on which order type needs it,
  // and in OPPOSITE directions -- easy to get backwards, so worth spelling
  // out. A plain "limit" order is this app's *resting* entry: "at this price
  // or lower" (see ORDER_TYPES' own title), a pullback below market for a
  // buy -- suggesting a price above market would make it marketable and
  // trip the ticket's own "fills immediately, use a Stop for a breakout"
  // warning, which is exactly what happened before this fix. So it goes
  // the SAME direction as the Stop trigger above -- below market for a buy
  // -- just with a smaller percentage (SUGGESTED_LIMIT_PCT) since it's
  // meant to be a near pullback, not a breakout level. A stop-limit's cap
  // is different: it needs to sit beyond its OWN trigger, in the trigger's
  // direction, or the entry could never actually fill once triggered --
  // anchoring it on current market price would suggest a limit already
  // behind the trigger for anything but a razor-thin trigger distance.
  // Reads the trigger the user actually has (typed or suggested) rather
  // than recomputing it blind, so retyping the trigger first moves the
  // limit suggestion along with it.
  useEffect(() => {
    if (suggestionReference === null) return;
    const triggerSuggested =
      side === "sell"
        ? suggestionReference * (1 - SUGGESTED_TRIGGER_PCT)
        : suggestionReference * (1 + SUGGESTED_TRIGGER_PCT);
    if (NEEDS_TRIGGER.has(orderType) && !triggerManuallyEdited) {
      setTriggerPrice(roundToCent(Math.max(triggerSuggested, 0.01)).toFixed(2));
    }
    if (NEEDS_LIMIT.has(orderType) && !limitManuallyEdited) {
      const suggested =
        orderType === "stop_limit"
          ? (() => {
              const trigger = numberOrUndefined(triggerPrice) ?? triggerSuggested;
              return side === "sell" ? trigger - SUGGESTED_LIMIT_OFFSET : trigger + SUGGESTED_LIMIT_OFFSET;
            })()
          : side === "sell"
            ? suggestionReference * (1 + SUGGESTED_LIMIT_PCT)
            : suggestionReference * (1 - SUGGESTED_LIMIT_PCT);
      setLimitPrice(roundToCent(Math.max(suggested, 0.01)).toFixed(2));
    }
  }, [orderType, side, suggestionReference, triggerPrice, limitManuallyEdited, triggerManuallyEdited]);

  // Publishes this ticket's current entry/stop/target to the chart as
  // indicative (draft) lines -- see CandleChart's indicativeLevels prop.
  // Entry prefers the server-resolved price once a preview exists (correct
  // for every order type, including stop/stop_limit, unlike
  // suggestionReference above which deliberately avoids that source to
  // dodge a feedback loop in the *suggestion* inputs -- this is a read-only
  // display value, so that concern doesn't apply here) and falls back to
  // the same reference the suggestions use before one does.
  useEffect(() => {
    if (!symbol) {
      setIndicativeLevels(null);
      return;
    }
    const entry = preview?.order?.entry_reference ?? suggestionReference ?? null;
    const stop = numberOrUndefined(stopPrice) ?? null;
    const target = numberOrUndefined(takeProfit) ?? null;
    if (entry == null && stop == null && target == null) {
      setIndicativeLevels(null);
      return;
    }
    setIndicativeLevels({
      symbol,
      side: side === "sell" ? "short" : "long",
      entry,
      stop,
      target,
      // Dragging a draft line on the chart writes straight back into these
      // same inputs, as if retyped -- so it also has to flip the manually-
      // edited flags, or the suggestion effects above would recompute and
      // overwrite the drag on the very next render.
      onDragStop: (price) => {
        setStopPrice(roundToCent(price).toFixed(2));
        setStopManuallyEdited(true);
      },
      onDragTarget: (price) => {
        setTakeProfit(roundToCent(price).toFixed(2));
        setTakeProfitManuallyEdited(true);
      },
    });
  }, [symbol, side, stopPrice, takeProfit, preview, suggestionReference, setIndicativeLevels]);

  // Separate from the effect above so this only fires on a real unmount
  // (e.g. switching away from the Ticket tab, which unmounts this
  // component) -- setIndicativeLevels is a useState setter and therefore a
  // stable reference, so this never re-fires on its own.
  useEffect(() => {
    return () => setIndicativeLevels(null);
  }, [setIndicativeLevels]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Minted once when the dialog opens, not per attempt: Alpaca rejects a
  // duplicate client_order_id, so retrying after a timeout resubmits the
  // *same* order rather than opening a second position. A server-generated
  // id would defeat that, since a retry would arrive with a new one.
  const clientOrderIdRef = useRef<string | null>(null);

  // Shared by the Q/W/E hotkeys below and their button-row equivalents in
  // the JSX further down -- one instant-fire path, two ways to trigger it.
  // Defined above the `if (!symbol) return` below (unlike doSubmit/
  // openConfirm further down, which can assume a symbol) since the hotkey
  // effect needs it before that point too. Reads `stopPrice` directly
  // (the same "By risk" field the confirm-gated flow uses) rather than
  // taking it as a parameter -- there's no keyboard-only way to pick a stop,
  // so it has to already be typed in.
  const fireInstant = async (side: "buy" | "sell", riskPct: number) => {
    if (!symbol || firing) return;
    const stop = numberOrUndefined(stopPrice);
    if (stop === undefined) {
      setError("Enter a stop price (By risk) before using the instant hotkeys.");
      return;
    }
    setFiring(true);
    clientOrderIdRef.current = randomUUID();
    try {
      const ticket: OrderTicketRequest = {
        symbol,
        side,
        order_type: "market",
        time_in_force: "day",
        risk: { stop_price: stop, risk_pct_of_equity: riskPct },
        // Rides along unchanged if a target's already typed in -- gives a
        // bracket order (DAS's "1:2 risk:reward" script's outcome) without
        // a dedicated hotkey.
        ...(takeProfit.trim() ? { take_profit_price: numberOrUndefined(takeProfit) } : {}),
        client_order_id: clientOrderIdRef.current,
      };
      const result = await submitOrder(ticket);
      setPlaced(result.order?.id ?? "submitted");
      setRejection(null);
      setError(null);
      onSubmitted();
    } catch (err: unknown) {
      if (err instanceof OrderRejectedError) {
        setRejection(err.detail);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setFiring(false);
    }
  };

  // DAS script #15 ("High of Day"): a breakout-entry stop order at the
  // day's high, risk-sized the same way fireInstant is, with a market
  // stop-loss BREAKOUT_STOP_OFFSET_PCT below the trigger attached
  // automatically (same risk.stop_price mechanism -- see that constant's
  // comment above for why this is a percentage of price, not DAS's flat
  // $0.30). Needs one round trip (dayHigh) before it can build the ticket,
  // unlike fireInstant -- acceptable since this places a resting stop
  // order, not an immediate fill, so the extra latency doesn't cost a
  // worse price.
  const fireBreakout = async () => {
    if (!symbol || firing) return;
    setFiring(true);
    setError(null);
    try {
      const high = await dayHigh(symbol);
      if (high === null) {
        setError("No day-high available for this symbol yet.");
        return;
      }
      // Alpaca rejects sub-penny prices above $1 -- round both to the cent
      // Alpaca actually prices in, not just for display. `trigger * (1 -
      // pct)` in particular lands on a binary-float artifact like
      // 20.110000000000003 far more often than not.
      const trigger = roundToCent(high + BREAKOUT_TRIGGER_OFFSET);
      const protectiveStop = roundToCent(trigger * (1 - BREAKOUT_STOP_OFFSET_PCT));
      clientOrderIdRef.current = randomUUID();
      const ticket: OrderTicketRequest = {
        symbol,
        side: "buy",
        order_type: "stop",
        time_in_force: "day",
        stop_price: trigger,
        risk: { stop_price: protectiveStop, risk_pct_of_equity: defaultRiskPct },
        client_order_id: clientOrderIdRef.current,
      };
      const result = await submitOrder(ticket);
      setPlaced(result.order?.id ?? "submitted");
      setRejection(null);
      onSubmitted();
    } catch (err: unknown) {
      if (err instanceof OrderRejectedError) {
        setRejection(err.detail);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setFiring(false);
    }
  };

  // Hotkeys: B/S for side, 1-4 for order type (matching ORDER_TYPES' order),
  // Enter to open the confirm dialog, Q/W/E (Shift+ for sell) to instant-
  // fire a risk-sized market order at INSTANT_FIRE_RISK_PCTS with no dialog
  // at all, T for a breakout-entry stop order. Placed above the `if
  // (!symbol) return` below so this hook always runs -- referencing
  // preview/submitting/confirming state directly rather than the
  // canSubmit/openConfirm consts defined further down, which only exist
  // once a symbol is selected.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = document.activeElement;
      const isTyping =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      // Modal.tsx already owns Escape on the confirm dialog; leave every key
      // alone while it's open rather than risk double-handling one of these.
      if (isTyping || confirming) return;

      const instantIndex = INSTANT_FIRE_KEYS.indexOf(e.key.toLowerCase());
      if (instantIndex !== -1) {
        e.preventDefault();
        void fireInstant(e.shiftKey ? "sell" : "buy", INSTANT_FIRE_RISK_PCTS[instantIndex]);
        return;
      }
      if (e.key === "t" || e.key === "T") {
        e.preventDefault();
        void fireBreakout();
        return;
      }

      if (e.key === "b" || e.key === "B") {
        setSide("buy");
      } else if (e.key === "s" || e.key === "S") {
        setSide("sell");
      } else if (e.key >= "1" && e.key <= String(ORDER_TYPES.length)) {
        setOrderType(ORDER_TYPES[Number(e.key) - 1].type);
      } else if (e.key === "Enter") {
        const canSubmitNow = Boolean(preview?.order && preview?.can_submit) && !submitting;
        if (!canSubmitNow) return;
        clientOrderIdRef.current = randomUUID();
        setPlaced(null);
        setConfirming(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirming, preview, submitting, symbol, firing, fireInstant, fireBreakout]);

  // Sanity bound for Risk %, not a hard broker limit -- the backend enforces
  // only gt=0 (no ceiling exists server-side), so this exists purely to
  // catch a fat-fingered value (50 typed for 0.5) before it prices as a real
  // order. Relative to the account's own default rather than a flat number,
  // with a 5% floor so a very low default doesn't make the guardrail trip on
  // ordinary values. Deliberately NOT keyed off preview.limits.default_risk_pct
  // -- that would put `missing` (an effect dependency) in a feedback loop
  // with the very preview the effect fetches: a blocked ticket clears
  // preview, which removes the reference, which unblocks it, which
  // re-fetches, forever.
  const riskPctCeiling = Math.max(5, defaultRiskPct * 5);
  const riskPctValue = numberOrUndefined(riskPct);

  // What the ticket still needs before it can be priced at all. Used both
  // to skip pointless preview requests and to say, on the button itself, why
  // nothing happens -- a control that silently refuses to act reads as
  // broken, which is exactly how the empty-stop case got reported.
  const missing: string | null = !symbol
    ? "Select a symbol"
    : NEEDS_TRIGGER.has(orderType) && !numberOrUndefined(triggerPrice)
      ? "Enter a trigger price — the order rests until price trades through it"
      : NEEDS_LIMIT.has(orderType) && !numberOrUndefined(limitPrice)
      ? "Enter a limit price"
      : sizingMode === "shares"
        ? !numberOrUndefined(qty)
          ? "Enter a quantity"
          : null
        : !numberOrUndefined(stopPrice)
          ? "Enter a stop price — it is what sizes the order"
          : riskPctValue === undefined
            ? "Enter a risk %"
            : riskPctValue > riskPctCeiling
              ? "Risk % looks too high — check the value before pricing"
              : null;

  useEffect(() => {
    if (!symbol) {
      setPreview(null);
      setRejection(null);
      return;
    }

    const ticket: OrderTicketRequest = {
      symbol,
      side,
      order_type: orderType,
      // Only when pinned. Left out, the server decides -- keeping that rule
      // in one place instead of restating it here where it could drift.
      ...(timeInForce ? { time_in_force: timeInForce } : {}),
      ...(NEEDS_LIMIT.has(orderType) ? { limit_price: numberOrUndefined(limitPrice) } : {}),
      ...(NEEDS_TRIGGER.has(orderType) ? { stop_price: numberOrUndefined(triggerPrice) } : {}),
      ...(takeProfit.trim() ? { take_profit_price: numberOrUndefined(takeProfit) } : {}),
      ...(sizingMode === "shares"
        ? { qty: numberOrUndefined(qty) }
        : {
            risk: {
              stop_price: numberOrUndefined(stopPrice) ?? 0,
              risk_pct_of_equity: numberOrUndefined(riskPct),
            },
          }),
    };

    // Don't ask the server to price a ticket that is obviously incomplete --
    // it would answer with a validation error the user hasn't earned yet.
    if (missing) {
      setPreview(null);
      setRejection(null);
      setPreviewPending(false);
      return;
    }

    if (timerRef.current) clearTimeout(timerRef.current);
    let cancelled = false;
    setPreviewPending(true);
    timerRef.current = setTimeout(() => {
      previewOrder(ticket)
        .then((result) => {
          if (cancelled) return;
          setPreview(result);
          setRejection(null);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setPreview(null);
          if (err instanceof OrderRejectedError) {
            setRejection(err.detail);
            setError(null);
          } else {
            setRejection(null);
            setError(err instanceof Error ? err.message : String(err));
          }
        })
        .finally(() => {
          if (cancelled) return;
          setPreviewPending(false);
        });
    }, PREVIEW_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [
    symbol,
    side,
    orderType,
    sizingMode,
    qty,
    limitPrice,
    triggerPrice,
    stopPrice,
    riskPct,
    takeProfit,
    missing,
  ]);

  if (!symbol) {
    return <div className="widget-empty">Select a symbol to build an order.</div>;
  }

  const order = preview?.order;
  // Reference price for the quick-% sizing buttons (#1) -- never fires an
  // extra preview request just to have a price; falls back to the position's
  // last known price, then gives up and disables the buttons.
  const sizingPrice = order?.entry_reference ?? num(position?.current_price) ?? null;
  const buyingPower = num(account?.buying_power);
  const exits = position ? exitsForPosition(position, orders) : null;

  // A synchronous, client-side "≈ N shares" estimate for risk mode, shown
  // while the authoritative server-priced qty in the preview panel is
  // stale or still in flight -- it must read as visually distinct from that
  // qty since the two can disagree during previewPending.
  const riskEntryRef = num(position?.current_price) ?? order?.entry_reference ?? null;
  const estimatedRiskShares = (() => {
    if (sizingMode !== "risk") return null;
    const equity = num(account?.equity);
    const stop = numberOrUndefined(stopPrice);
    const pct = numberOrUndefined(riskPct);
    if (equity == null || stop == null || pct == null || riskEntryRef == null) return null;
    const riskPerShare = Math.abs(riskEntryRef - stop);
    if (riskPerShare <= 0) return null;
    return Math.floor((equity * (pct / 100)) / riskPerShare);
  })();
  // Drives the instant-fire buy/sell buttons' disabled state and tooltip --
  // fireInstant itself checks the same thing, this just lets the button say
  // why up front instead of firing and immediately erroring.
  const hasInstantStop = numberOrUndefined(stopPrice) !== undefined;
  const canSubmit = Boolean(order && preview?.can_submit) && !submitting;
  const disabledReason = canSubmit
    ? null
    : (missing ??
      (preview && !preview.can_submit
        ? "Order placement is switched off. Set TRADING_ENABLED=true in backend/.env and restart."
        : null));

  const openConfirm = () => {
    clientOrderIdRef.current = randomUUID();
    setPlaced(null);
    setConfirming(true);
  };

  const doSubmit = async () => {
    if (!order) return;
    setSubmitting(true);
    try {
      const ticket: OrderTicketRequest = {
        symbol: order.symbol,
        side: order.side as "buy" | "sell",
        order_type: order.order_type as EntryOrderType,
        // Carried from the priced order rather than recomputed: the user
        // confirmed a ticket that said gtc or day, and submitting without it
        // would silently fall back to the default -- which is how a bracket
        // ends up as a day order nobody chose.
        time_in_force: order.time_in_force as "day" | "gtc",
        ...(order.limit_price !== null ? { limit_price: order.limit_price } : {}),
        ...(order.stop_price !== null ? { stop_price: order.stop_price } : {}),
        ...(order.take_profit_price !== null ? { take_profit_price: order.take_profit_price } : {}),
        // Submit the resolved quantity rather than re-sending the risk inputs:
        // the user confirmed a specific size, and re-sizing server-side could
        // silently place a different one if the price moved between the
        // preview and the click.
        qty: order.qty,
        ...(order.stop_loss_price !== null ? { stop_loss_price: order.stop_loss_price } : {}),
        client_order_id: clientOrderIdRef.current ?? undefined,
      };
      const result = await submitOrder(ticket);
      setPlaced(result.order?.id ?? "submitted");
      setRejection(null);
      setError(null);
      setConfirming(false);
      onSubmitted();
    } catch (err: unknown) {
      if (err instanceof OrderRejectedError) {
        setRejection(err.detail);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  };

  // What the ticket will actually be sent as: the server's own answer once it
  // has priced one, the same rule applied locally before that.
  const effectiveTimeInForce =
    (preview?.order.time_in_force as "day" | "gtc" | undefined) ??
    timeInForce ??
    (takeProfit.trim() || sizingMode === "risk" ? "gtc" : "day");

  return (
    <div className="order-ticket">
      <div className="order-ticket-row">
        <span className="order-ticket-symbol">{symbol}</span>
        <div className="timeframe-selector side-toggle">
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={side === "buy"}
            onClick={() => setSide("buy")}
            title="Hotkey: B"
          >
            Buy
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={side === "sell"}
            onClick={() => setSide("sell")}
            title="Hotkey: S"
          >
            Sell
          </button>
        </div>
      </div>

      <div className="order-ticket-row">
        <div
          className="timeframe-selector instant-fire-row"
          title={
            hasInstantStop
              ? "Market order, sized to this risk % of equity off the Stop below -- no confirmation dialog, fires immediately."
              : "Enter a Stop price below first -- instant hotkeys size off it."
          }
        >
          {INSTANT_FIRE_RISK_PCTS.map((pct, i) => (
            <button
              key={`buy-${pct}`}
              type="button"
              className="timeframe-button instant-fire-buy"
              disabled={!symbol || firing || !hasInstantStop}
              onClick={() => void fireInstant("buy", pct)}
              title={`Hotkey: ${INSTANT_FIRE_KEYS[i].toUpperCase()}`}
            >
              Buy {pct}%
            </button>
          ))}
        </div>
        <div className="timeframe-selector instant-fire-row">
          {INSTANT_FIRE_RISK_PCTS.map((pct, i) => (
            <button
              key={`sell-${pct}`}
              type="button"
              className="timeframe-button instant-fire-sell"
              disabled={!symbol || firing || !hasInstantStop}
              onClick={() => void fireInstant("sell", pct)}
              title={`Hotkey: Shift+${INSTANT_FIRE_KEYS[i].toUpperCase()}`}
            >
              Sell {pct}%
            </button>
          ))}
        </div>
        <div className="timeframe-selector instant-fire-row">
          <button
            type="button"
            className="timeframe-button instant-fire-buy"
            disabled={!symbol || firing}
            onClick={() => void fireBreakout()}
            title="Hotkey: T -- buy stop above today's high, risk-sized, stop-loss attached."
          >
            Breakout
          </button>
        </div>
      </div>
      <div className="order-hint">
        Instant, no dialog -- risk % is off the Stop field below. Positions tab has
        Flatten (F) / Cancel all (C) / Stop to BE (0) / BE+offset (Shift+0) / Scale out (X).
      </div>

      <div className="order-ticket-context">
        <span>
          Buying power <strong>{money(account?.buying_power)}</strong>
        </span>
        <span>
          Equity <strong>{money(account?.equity)}</strong>
        </span>
        {position && (
          <span>
            Holding <strong>{position.qty}</strong> @ {money(position.avg_entry_price)}
          </span>
        )}
      </div>

      {exits && (exits.stopLoss !== null || exits.takeProfit !== null) && !existingExitsDismissed && (
        <div className="order-rejection" role="status">
          {symbol} already has
          {exits.stopLoss !== null ? ` a stop at ${formatPrice(exits.stopLoss)}` : ""}
          {exits.stopLoss !== null && exits.takeProfit !== null ? " and" : ""}
          {exits.takeProfit !== null ? ` a target at ${formatPrice(exits.takeProfit)}` : ""}. A new
          order here does not replace {exits.stopLoss !== null && exits.takeProfit !== null ? "them" : "it"}.{" "}
          <button
            type="button"
            className="row-action"
            onClick={() => setExistingExitsDismissed(true)}
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="order-ticket-row">
        <div className="timeframe-selector">
          {ORDER_TYPES.map(({ type, label, title }, i) => (
            <button
              key={type}
              type="button"
              className="timeframe-button"
              aria-pressed={orderType === type}
              onClick={() => setOrderType(type)}
              title={`${title} Hotkey: ${i + 1}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="timeframe-selector">
          {(["day", "gtc"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className="timeframe-button"
              aria-pressed={effectiveTimeInForce === t}
              onClick={() => setTimeInForce(t)}
              title={
                t === "day"
                  ? "Expires at the close -- including any take-profit and stop-loss legs, which leaves an overnight position unprotected."
                  : "Stays working until filled or cancelled, so the protective legs survive the close."
              }
            >
              {t === "day" ? "Day" : "GTC"}
            </button>
          ))}
        </div>
      </div>

      {(NEEDS_TRIGGER.has(orderType) || NEEDS_LIMIT.has(orderType)) && (
        <div className="order-ticket-row">
          {NEEDS_TRIGGER.has(orderType) && (
            <label title="The order rests until price trades through this, then goes in.">
              Trigger
              <input
                type="number"
                step="0.01"
                value={triggerPrice}
                onChange={(e) => {
                  setTriggerPrice(e.target.value);
                  setTriggerManuallyEdited(true);
                }}
              />
              <span className="order-hint">rests until price trades through it</span>
            </label>
          )}
          {NEEDS_LIMIT.has(orderType) && (
            <label
              title={
                orderType === "stop_limit"
                  ? "Once triggered, the most a buy pays / least a sell takes."
                  : "Buy at this or lower; sell at this or higher. Above the market on a buy, it fills immediately."
              }
            >
              Limit
              <input
                type="number"
                step="0.01"
                value={limitPrice}
                onChange={(e) => {
                  setLimitPrice(e.target.value);
                  setLimitManuallyEdited(true);
                }}
              />
              <span className="order-hint">
                {orderType === "stop_limit" ? "cap once triggered" : "fills at this price or better"}
              </span>
            </label>
          )}
        </div>
      )}

      <div className="order-ticket-row">
        <div className="timeframe-selector">
          {(["risk", "shares"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className="timeframe-button"
              aria-pressed={sizingMode === m}
              onClick={() => setSizingMode(m)}
            >
              {m === "risk" ? "By risk" : "Shares"}
            </button>
          ))}
        </div>
      </div>

      {sizingMode === "shares" ? (
        <div className="order-ticket-row">
          <label>
            Qty
            <input type="number" step="1" value={qty} onChange={(e) => setQty(e.target.value)} />
          </label>
          <div
            className="timeframe-selector"
            title={
              sizingPrice == null
                ? "No reference price yet -- pick an order type/price or wait for a position price."
                : buyingPower == null
                  ? "Buying power unavailable."
                  : undefined
            }
          >
            {[25, 50, 75, 100].map((pct) => (
              <button
                key={pct}
                type="button"
                className="timeframe-button"
                disabled={sizingPrice == null || buyingPower == null}
                onClick={() => {
                  if (sizingPrice == null || buyingPower == null) return;
                  const shares = Math.floor(((buyingPower * pct) / 100) / sizingPrice);
                  setQty(String(Math.max(0, shares)));
                }}
              >
                {pct}%
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="order-ticket-row">
          <label>
            Stop
            <input
              type="number"
              step="0.01"
              value={stopPrice}
              onChange={(e) => {
                setStopPrice(e.target.value);
                setStopManuallyEdited(true);
              }}
            />
          </label>
          <label>
            Risk %
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="10"
              value={riskPct}
              onChange={(e) => setRiskPct(e.target.value)}
            />
            {estimatedRiskShares !== null && (
              <span className="order-hint" style={{ fontStyle: "italic" }}>
                ≈ {estimatedRiskShares.toLocaleString()} sh
              </span>
            )}
          </label>
        </div>
      )}

      <div className="order-ticket-row">
        <label>
          Target
          <input
            type="number"
            step="0.01"
            value={takeProfit}
            onChange={(e) => {
              setTakeProfit(e.target.value);
              setTakeProfitManuallyEdited(true);
            }}
          />
        </label>
      </div>

      {rejection && (
        <div className="order-rejection" role="status">
          {rejection.message}
        </div>
      )}
      {error && <div className="order-rejection">{error}</div>}

      {previewPending && <div className="order-hint">Pricing…</div>}
      {firing && <div className="order-hint">Firing instant order…</div>}

      {order && (
        <div className="order-preview">
          <strong>
            {order.qty.toLocaleString()} sh · {order.notional.toFixed(2)}
          </strong>
          <span>
            {order.order_class !== "simple" ? `${order.order_class} · ` : ""}
            @ {order.entry_reference.toFixed(2)}
          </span>
          {order.risk_amount !== null && (
            <span>
              risk {order.risk_amount.toFixed(2)}
              {order.risk_pct_of_equity !== null ? ` (${order.risk_pct_of_equity}% of equity)` : ""}
              {order.risk_per_share !== null ? ` · ${order.risk_per_share.toFixed(2)}/sh` : ""}
            </span>
          )}
          {preview?.limits && (
            <span>
              Ceilings: {preview.limits.max_order_qty.toLocaleString()} sh /{" "}
              {preview.limits.max_order_notional.toLocaleString()} notional
            </span>
          )}
        </div>
      )}

      {order?.warnings.map((w) => (
        <div key={w} className="order-warning" role="status">
          {w}
        </div>
      ))}

      {placed && <div className="order-preview">Order submitted.</div>}

      {disabledReason && <div className="order-hint">{disabledReason}</div>}

      <button
        type="button"
        className="generate-button"
        disabled={!canSubmit}
        onClick={openConfirm}
        title={disabledReason ?? "Hotkey: Enter"}
      >
        {submitting ? "Submitting…" : `${side === "buy" ? "Buy" : "Sell"} ${symbol}`}
      </button>

      <Modal open={confirming} title="Confirm order" onClose={() => setConfirming(false)}>
        {order && (
          <div className="order-confirm">
            <p className="order-confirm-line">
              <strong>
                {order.side.toUpperCase()} {order.qty.toLocaleString()} {order.symbol}
              </strong>{" "}
              {order.order_type.replace("_", "-")}
              {order.stop_price !== null ? ` · trigger ${order.stop_price}` : ""}
              {order.limit_price !== null ? ` @ ${order.limit_price}` : ""}
            </p>
            {order.warnings.map((w) => (
              <p key={w} className="order-warning">
                {w}
              </p>
            ))}
            <p className="order-confirm-line">
              Notional {order.notional.toFixed(2)}
              {order.risk_amount !== null ? ` · risk ${order.risk_amount.toFixed(2)}` : ""}
              {order.risk_pct_of_equity !== null ? ` (${order.risk_pct_of_equity}% of equity)` : ""}
            </p>
            {(order.stop_loss_price !== null || order.take_profit_price !== null) && (
              <p className="order-confirm-line">
                {order.stop_loss_price !== null ? `Stop ${order.stop_loss_price}` : ""}
                {order.stop_loss_price !== null && order.take_profit_price !== null ? " · " : ""}
                {order.take_profit_price !== null ? `Target ${order.take_profit_price}` : ""}
              </p>
            )}
            <p className="order-confirm-mode">PAPER — simulated account</p>
            <div className="order-confirm-actions">
              <button type="button" className="timeframe-button" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="generate-button"
                disabled={submitting}
                onClick={() => void doSubmit()}
              >
                {submitting ? "Submitting…" : "Place order"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
