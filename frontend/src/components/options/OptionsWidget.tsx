import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import { TICKET_MAX_WIDTH, TICKET_MIN_WIDTH, clampShortTarget, getSettings, updateSettings } from "../../api/settings";
import { modeBadge, setTradingMode, type TradingMode } from "../../api/tradingMode";
import { useOptionChain } from "../../hooks/useOptionChain";
import { useOptionsIdeas } from "../../hooks/useOptionsIdeas";
import { useReplaySession } from "../../hooks/useReplaySession";
import { useSpreads } from "../../hooks/useSpreads";
import {
  SHORT_DELTA_MAX,
  SHORT_DELTA_MIN,
  SHORT_OFFSET_MAX,
  TIME_STRATEGIES,
  shortTargetGroup,
  type OptionKind,
  type ShortTarget,
  type ShortTargetGroup,
  type Strategy,
} from "../../types/options";
import { isSymbolDrag, readDroppedSymbol } from "../../utils/dragSymbol";
import { formatExpiry, type ParsedOcc } from "../../utils/occ";
import type { OptionsIdea } from "../../types/options";
import { AiIdeaTab } from "./AiIdeaTab";
import { ChainTable } from "./ChainTable";
import {
  applyPick,
  defaultLegs,
  legsFromTicket,
  selectionOf,
  strategyKind,
  type Legs,
  type PickContext,
} from "./legPicker";
import { BrokerMissing } from "../common/BrokerMissing";
import { OpenSpreads } from "./OpenSpreads";
import { OptionOrders } from "./OptionOrders";
import { SpreadTicket } from "./SpreadTicket";

type Tab = "chain" | "spreads" | "idea";

interface OptionsWidgetProps {
  symbol: string | null;
  mode: TradingMode;
  onSelectSymbol?: (symbol: string) => void;
  /** The contract on the chart, if the chart shows a premium chart: the
   * widget follows it -- its expiry, and the strike as a long call/put. */
  focusContract?: ParsedOcc | null;
}

const STRATEGY_HOTKEYS: Strategy[] = ["bull_call", "bear_put", "bull_put", "bear_call", "iron_condor"];
/** A calendar's long leg defaults to the first expiry at least this many
 * days after the short one. */
const LONG_EXPIRY_MIN_GAP_DAYS = 7;

/** The Options widget: the chain picker with the ticket beside it, and
 * the open positions with their close and trigger controls. In Simulation
 * mode the same widget trades the local options book (live prices, or the
 * replayed moment during a history replay); in Live mode every action
 * asks for the typed confirmation. */
export function OptionsWidget({ symbol, mode, onSelectSymbol, focusContract }: OptionsWidgetProps) {
  const enabled = true;
  const [tab, setTab] = useState<Tab>("chain");
  // The Idea tab's request lives here so "Load into ticket" (which shows
  // the Chain tab) does not throw away an answer that took minutes.
  const ideas = useOptionsIdeas();
  // A replay only reaches this widget in Simulation mode: Paper and Live
  // keep showing the real account's chain, which next to a replayed chart
  // reads as "the chain does not move". Say so, and offer the switch.
  const replaySession = useReplaySession();
  const replayButLive = replaySession !== null && mode !== "simulation";
  const [strategy, setStrategy] = useState<Strategy>("bull_put");
  const [width, setWidth] = useState(2);
  const [legs, setLegs] = useState<Legs | null>(null);
  // Calendar/diagonal: the kind traded and which expiry a chain click sets.
  const [timeKind, setTimeKind] = useState<OptionKind>("call");
  const [picking, setPicking] = useState<"short" | "long">("short");
  // Set once the user has clicked a strike; the auto-pick then leaves the
  // legs alone until symbol/expiry/strategy changes.
  const manualRef = useRef(false);

  const isTime = TIME_STRATEGIES.has(strategy);
  const chainState = useOptionChain(symbol, enabled);
  // The later expiry's chain for a calendar/diagonal: a second instance of
  // the same hook (its expiries fetch is served from the server cache).
  const longChainState = useOptionChain(symbol, enabled && isTime);
  const spreads = useSpreads(enabled);
  const badge = modeBadge(mode);
  const replayFeed = spreads.account?.feed === "replay";
  const { chain, expiries, expiry, setExpiry } = chainState;

  // The long expiry: the first one a week or more after the short expiry,
  // unless the user chose one that still lies after the short expiry.
  useEffect(() => {
    if (!isTime || !expiry || longChainState.expiries.length === 0) return;
    const current = longChainState.expiry;
    if (current && current > expiry) return;
    const shortDte = expiries.find((e) => e.expiry === expiry)?.dte ?? 0;
    const preferred =
      longChainState.expiries.find((e) => e.expiry > expiry && e.dte >= shortDte + LONG_EXPIRY_MIN_GAP_DAYS) ??
      longChainState.expiries.find((e) => e.expiry > expiry) ??
      null;
    if (preferred && preferred.expiry !== current) longChainState.setExpiry(preferred.expiry);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTime, expiry, expiries, longChainState.expiries, longChainState.expiry]);

  // How far out the auto-pick puts short legs, per strategy group; kept in
  // the settings so the corridor a user likes survives a reload.
  const [shortTargets, setShortTargets] = useState<Record<ShortTargetGroup, ShortTarget>>(
    () => getSettings().optionsShortTargets,
  );
  const shortGroup = shortTargetGroup(strategy);
  const shortTarget = shortGroup ? shortTargets[shortGroup] : undefined;
  const setShortTarget = (target: ShortTarget) => {
    if (!shortGroup) return;
    const next = { ...shortTargets, [shortGroup]: clampShortTarget(target) };
    setShortTargets(next);
    updateSettings({ optionsShortTargets: next });
    // A new distance is a request for a new default pick.
    manualRef.current = false;
  };
  const stepShortTarget = (direction: 1 | -1) => {
    if (!shortTarget) return;
    // "Further out" is a smaller delta but a larger strike offset.
    if (shortTarget.mode === "delta") {
      setShortTarget({ mode: "delta", value: Math.min(SHORT_DELTA_MAX, Math.max(SHORT_DELTA_MIN, shortTarget.value - 0.05 * direction)) });
    } else {
      setShortTarget({ mode: "offset", value: Math.min(SHORT_OFFSET_MAX, Math.max(0, shortTarget.value + direction)) });
    }
  };

  const ctx = useMemo<PickContext>(
    () => ({
      timeKind,
      longExpiry: isTime ? longChainState.expiry : null,
      longChain: isTime ? longChainState.chain : null,
      picking,
      shortTarget: shortTarget ?? null,
    }),
    [timeKind, isTime, longChainState.expiry, longChainState.chain, picking, shortTarget],
  );

  // Auto-pick on a new symbol/expiry/strategy/width, or the first chain.
  useEffect(() => {
    manualRef.current = false;
    setPicking("short");
  }, [symbol, expiry, strategy, timeKind]);
  useEffect(() => {
    if (!chain) {
      setLegs(null);
      return;
    }
    if (manualRef.current) return;
    setLegs(defaultLegs(strategy, chain, width, ctx));
    // Only the inputs of the default pick, not `legs` itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chain, strategy, width, ctx.timeKind, ctx.longExpiry, ctx.longChain, ctx.shortTarget]);

  // Following the chart's contract happens in three steps as the data
  // arrives: strategy at once, the expiry once the list holds it, the
  // strike once that expiry's chain is in. The pending ref carries the
  // request across those renders and is cleared when the strike lands.
  const pendingFocusRef = useRef<ParsedOcc | null>(null);
  const focusKey = focusContract
    ? `${focusContract.underlying}:${focusContract.expiry}:${focusContract.kind}:${focusContract.strike}`
    : null;
  useEffect(() => {
    if (!focusContract || focusContract.underlying !== symbol) return;
    pendingFocusRef.current = focusContract;
    setStrategy(focusContract.kind === "call" ? "long_call" : "long_put");
    setTab("chain");
    // focusKey stands in for the object, which is rebuilt every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, symbol]);
  useEffect(() => {
    const pending = pendingFocusRef.current;
    if (!pending || expiries.length === 0) return;
    if (expiry !== pending.expiry && expiries.some((e) => e.expiry === pending.expiry)) setExpiry(pending.expiry);
  }, [expiries, expiry, setExpiry]);
  useEffect(() => {
    const pending = pendingFocusRef.current;
    if (!pending || !chain || chain.expiry !== pending.expiry || chain.underlying !== pending.underlying) return;
    manualRef.current = true;
    setLegs({ strike: pending.strike });
    pendingFocusRef.current = null;
  }, [chain]);

  // Loading a suggested structure (the Idea tab) is the same three-step
  // dance as following the chart's contract above, and for the same reason:
  // the auto-pick effect clears manualRef on every strategy/expiry change,
  // so the legs can only be planted once the chain for that expiry is
  // actually in. Setting them any earlier means watching the auto-pick
  // overwrite them a render later.
  const pendingIdeaRef = useRef<{ legs: Legs; expiry: string; longExpiry?: string } | null>(null);
  const loadIdea = (idea: OptionsIdea): boolean => {
    if (!symbol || idea.ticket.underlying !== symbol) return false;
    const resolved = legsFromTicket(idea.strategy, idea.ticket);
    if (!resolved) return false;
    if (!expiries.some((e) => e.expiry === idea.ticket.expiry)) return false;

    pendingIdeaRef.current = { ...resolved, expiry: idea.ticket.expiry };
    // A calendar or diagonal is traded in one kind; the ticket's legs say
    // which, and the widget's own toggle has to agree or the pick context
    // describes a different spread.
    if (TIME_STRATEGIES.has(idea.strategy) && idea.ticket.legs?.[0]) {
      setTimeKind(idea.ticket.legs[0].kind);
    }
    setStrategy(idea.strategy);
    setTab("chain");
    return true;
  };
  useEffect(() => {
    const pending = pendingIdeaRef.current;
    if (!pending) return;
    if (expiry !== pending.expiry) setExpiry(pending.expiry);
    if (pending.longExpiry && longChainState.expiry !== pending.longExpiry) {
      longChainState.setExpiry(pending.longExpiry);
    }
  }, [expiry, setExpiry, longChainState]);
  useEffect(() => {
    const pending = pendingIdeaRef.current;
    if (!pending || !chain || chain.expiry !== pending.expiry || chain.underlying !== symbol) return;
    // A time spread also needs its far chain, or its legs mean nothing.
    if (pending.longExpiry && longChainState.chain?.expiry !== pending.longExpiry) return;
    manualRef.current = true;
    setLegs(pending.legs);
    pendingIdeaRef.current = null;
  }, [chain, symbol, longChainState.chain]);

  const selection = useMemo(() => selectionOf(strategy, legs, ctx), [strategy, legs, ctx]);

  // The chain on screen: the long expiry's while picking the long leg of
  // a calendar/diagonal, the ticket's expiry otherwise.
  const shownChain = isTime && picking === "long" ? longChainState.chain : chain;

  const pick = (kind: OptionKind, strike: number) => {
    const target = shownChain;
    if (!target) return;
    manualRef.current = true;
    setLegs((current) => applyPick(strategy, current, target, kind, strike, ctx));
  };

  const resetLegs = () => {
    manualRef.current = false;
    if (chain) setLegs(defaultLegs(strategy, chain, width, ctx));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (mode === "live" || !enabled) return;
    const target = e.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable) return;
    if (e.key === "[" || e.key === "]") {
      if (!expiry || expiries.length === 0) return;
      const i = expiries.findIndex((x) => x.expiry === expiry);
      const next = expiries[i + (e.key === "]" ? 1 : -1)];
      if (next) setExpiry(next.expiry);
      e.preventDefault();
    } else if (e.key >= "5" && e.key <= "9") {
      setStrategy(STRATEGY_HOTKEYS[Number(e.key) - 5]);
      e.preventDefault();
    } else if (e.key === "+" || e.key === "=") {
      if (e.shiftKey) stepShortTarget(1);
      else setWidth((w) => Math.min(20, w + 1));
      e.preventDefault();
    } else if (e.key === "-" || e.key === "_") {
      if (e.shiftKey) stepShortTarget(-1);
      else setWidth((w) => Math.max(1, w - 1));
      e.preventDefault();
    }
  };

  const openCount = spreads.spreads.length;

  // The ticket column's width: dragged at the splitter between the chain
  // and the ticket (left = wider ticket, so the risk chart grows), kept in
  // the settings. Pointer capture keeps the drag alive over the chain.
  const [ticketWidth, setTicketWidth] = useState(() => getSettings().optionsTicketWidth);
  const layoutRef = useRef<HTMLDivElement | null>(null);
  const onSplitterDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const layout = layoutRef.current;
    if (!layout) return;
    e.preventDefault();
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    const right = layout.getBoundingClientRect().right;
    let latest = ticketWidth;
    const move = (ev: PointerEvent) => {
      latest = Math.min(TICKET_MAX_WIDTH, Math.max(TICKET_MIN_WIDTH, Math.round(right - ev.clientX)));
      setTicketWidth(latest);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      updateSettings({ optionsTicketWidth: latest });
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  };

  return (
    <div
      className={`widget options-widget${mode === "live" ? " live-frame" : ""}`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onDragOver={(e) => {
        if (!onSelectSymbol || !isSymbolDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDrop={(e) => {
        if (!onSelectSymbol) return;
        const dropped = readDroppedSymbol(e);
        if (!dropped) return;
        e.preventDefault();
        onSelectSymbol(dropped);
      }}
    >
      <div className="widget-header">
        <h2>Options</h2>
        <span
          className={`trading-mode-badge ${badge.className}`}
          title={replayFeed ? `Simulated book priced from the replay clock${spreads.account?.replay_as_of ? ` (${new Date(spreads.account.replay_as_of).toLocaleString()})` : ""}` : undefined}
        >
          {badge.label}
          {replayFeed ? " · REPLAY" : ""}
        </span>
        <div className="timeframe-selector">
          <button type="button" className="timeframe-button" aria-pressed={tab === "chain"} onClick={() => setTab("chain")}>
            Chain
          </button>
          <button
            type="button"
            className="timeframe-button"
            aria-pressed={tab === "spreads"}
            onClick={() => setTab("spreads")}
          >
            Open spreads{openCount > 0 ? ` (${openCount})` : ""}
          </button>
          {/* Not offered in Simulation mode: a replayed chain is synthetic
            * (bid/ask derived from the last print, IV solved back out of it,
            * no open interest), so a structure suggested on it would look
            * far better founded than it is. */}
          {mode !== "simulation" && (
            <button
              type="button"
              className="timeframe-button"
              aria-pressed={tab === "idea"}
              onClick={() => setTab("idea")}
              title="Ask Claude for option structures on this chain"
              aria-busy={ideas.loading}
            >
              {ideas.loading ? "Idea…" : "Idea"}
            </button>
          )}
        </div>
        {spreads.account && (
          <span className="widget-count" title="Options buying power · options trading level · data feed">
            BP {spreads.account.options_buying_power?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? "—"} ·
            L{spreads.account.options_trading_level ?? "?"} · {spreads.account.feed}
          </span>
        )}
      </div>
      <div className="widget-body">
        {replayButLive && (
          <div className="widget-empty replay-mode-hint">
            A replay is running, but this chain is the live {mode === "live" ? "Live" : "Paper"} account's.{" "}
            <button type="button" className="row-action" onClick={() => setTradingMode("simulation")}>
              Switch to Simulation
            </button>{" "}
            to see the chain as it was at the replayed moment.
          </div>
        )}
        {mode !== "simulation" && spreads.brokerMissing ? (
          <BrokerMissing mode={mode} />
        ) : tab === "spreads" ? (
          <>
            {mode === "simulation" && <OptionOrders onChanged={spreads.afterAction} />}
            <OpenSpreads
              spreads={spreads.spreads}
              triggers={spreads.triggers}
              account={spreads.account}
              mode={mode}
              symbol={symbol}
              loading={spreads.loading}
              error={spreads.error}
              onClose={spreads.close}
              onArm={spreads.armTrigger}
              onCancelTrigger={spreads.cancelTrigger}
              onSelectSymbol={onSelectSymbol}
            />
          </>
        ) : tab === "idea" ? (
          <AiIdeaTab symbol={symbol} ideas={ideas} onLoad={loadIdea} />
        ) : !symbol ? (
          <div className="widget-empty">Select a symbol in a scanner or the watchlist to load its option chain.</div>
        ) : (
          <>
            <div className="expiry-strip timeframe-selector">
              {expiries.map((e) => (
                <button
                  key={e.expiry}
                  type="button"
                  className="timeframe-button"
                  aria-pressed={e.expiry === expiry}
                  onClick={() => setExpiry(e.expiry)}
                  title={`${e.expiry} · ${e.contract_count} contracts${isTime ? " (short leg)" : ""}`}
                >
                  {formatExpiry(e.expiry)} <small>{e.dte}d</small>
                </button>
              ))}
              {mode !== "live" && (
                <span className="order-hint">[ ] expiry · 5–9 strategy · + − width</span>
              )}
            </div>
            {chainState.error && <p className="order-rejection">{chainState.error}</p>}
            {isTime && longChainState.error && <p className="order-rejection">{longChainState.error}</p>}
            <div
              className="options-chain-layout"
              ref={layoutRef}
              style={{ gridTemplateColumns: `minmax(0, 1fr) 8px ${ticketWidth}px` }}
            >
              {shownChain ? (
                <ChainTable chain={shownChain} selection={selection} pickable={strategyKind(strategy, timeKind)} onPick={pick} />
              ) : (
                <div className="widget-empty">
                  {chainState.loading || longChainState.loading ? "Loading chain…" : "No chain."}
                </div>
              )}
              <div
                className="options-splitter"
                role="separator"
                aria-orientation="vertical"
                title="Drag to resize the ticket (and its risk chart)"
                onPointerDown={onSplitterDown}
                onDoubleClick={() => {
                  setTicketWidth(360);
                  updateSettings({ optionsTicketWidth: 360 });
                }}
              />
              {expiry && (
                <SpreadTicket
                  symbol={symbol}
                  expiry={expiry}
                  chain={chain}
                  strategy={strategy}
                  onStrategy={setStrategy}
                  width={width}
                  onWidth={setWidth}
                  legs={legs}
                  onResetLegs={resetLegs}
                  account={spreads.account}
                  mode={mode}
                  onSubmitted={spreads.afterAction}
                  onSelectSymbol={onSelectSymbol}
                  ctx={ctx}
                  expiries={expiries}
                  onTimeKind={setTimeKind}
                  onLongExpiry={(value) => {
                    manualRef.current = false;
                    if (value) longChainState.setExpiry(value);
                  }}
                  onPicking={setPicking}
                  shortTarget={shortTarget}
                  onShortTarget={setShortTarget}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
