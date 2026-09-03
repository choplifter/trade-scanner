import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { modeBadge, type TradingMode } from "../../api/tradingMode";
import { useOptionChain } from "../../hooks/useOptionChain";
import { useSpreads } from "../../hooks/useSpreads";
import { TIME_STRATEGIES, type OptionKind, type Strategy } from "../../types/options";
import { isSymbolDrag, readDroppedSymbol } from "../../utils/dragSymbol";
import { formatExpiry, type ParsedOcc } from "../../utils/occ";
import { ChainTable } from "./ChainTable";
import { applyPick, defaultLegs, selectionOf, strategyKind, type Legs, type PickContext } from "./legPicker";
import { OpenSpreads } from "./OpenSpreads";
import { SpreadTicket } from "./SpreadTicket";

type Tab = "chain" | "spreads";

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
 * the open positions with their close and trigger controls. Nothing here
 * runs in Simulation mode -- there is no sim options book -- and in Live
 * mode every action asks for the typed confirmation. */
export function OptionsWidget({ symbol, mode, onSelectSymbol, focusContract }: OptionsWidgetProps) {
  const enabled = mode !== "simulation";
  const [tab, setTab] = useState<Tab>("chain");
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

  const ctx = useMemo<PickContext>(
    () => ({
      timeKind,
      longExpiry: isTime ? longChainState.expiry : null,
      longChain: isTime ? longChainState.chain : null,
      picking,
    }),
    [timeKind, isTime, longChainState.expiry, longChainState.chain, picking],
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
  }, [chain, strategy, width, ctx.timeKind, ctx.longExpiry, ctx.longChain]);

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
      setWidth((w) => Math.min(20, w + 1));
      e.preventDefault();
    } else if (e.key === "-") {
      setWidth((w) => Math.max(1, w - 1));
      e.preventDefault();
    }
  };

  const openCount = spreads.spreads.length;

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
        <span className={`trading-mode-badge ${badge.className}`}>{badge.label}</span>
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
        </div>
        {spreads.account && (
          <span className="widget-count" title="Options buying power · options trading level · data feed">
            BP {spreads.account.options_buying_power?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? "—"} ·
            L{spreads.account.options_trading_level ?? "?"} · {spreads.account.feed}
          </span>
        )}
      </div>
      <div className="widget-body">
        {!enabled ? (
          <div className="widget-empty">
            Options trading is not available in Simulation mode. Switch to Paper (or Live) to see the chain and
            trade spreads.
          </div>
        ) : tab === "spreads" ? (
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
            <div className="options-chain-layout">
              {shownChain ? (
                <ChainTable chain={shownChain} selection={selection} pickable={strategyKind(strategy, timeKind)} onPick={pick} />
              ) : (
                <div className="widget-empty">
                  {chainState.loading || longChainState.loading ? "Loading chain…" : "No chain."}
                </div>
              )}
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
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
