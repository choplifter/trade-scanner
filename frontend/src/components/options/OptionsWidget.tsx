import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { modeBadge, type TradingMode } from "../../api/tradingMode";
import { useOptionChain } from "../../hooks/useOptionChain";
import { useSpreads } from "../../hooks/useSpreads";
import type { OptionKind, Strategy } from "../../types/options";
import { formatExpiry } from "../../utils/occ";
import { ChainTable } from "./ChainTable";
import { applyPick, defaultLegs, selectionOf, strategyKind, type Legs } from "./legPicker";
import { OpenSpreads } from "./OpenSpreads";
import { SpreadTicket } from "./SpreadTicket";

type Tab = "chain" | "spreads";

interface OptionsWidgetProps {
  symbol: string | null;
  mode: TradingMode;
  onSelectSymbol?: (symbol: string) => void;
}

const STRATEGY_HOTKEYS: Strategy[] = ["bull_call", "bear_put", "bull_put", "bear_call", "iron_condor"];

/** The Options widget: the chain picker with the spread ticket beside it,
 * and the open spreads with their close and trigger controls. Nothing here
 * runs in Simulation mode -- there is no sim options book -- and in Live
 * mode every action asks for the typed confirmation. */
export function OptionsWidget({ symbol, mode, onSelectSymbol }: OptionsWidgetProps) {
  const enabled = mode !== "simulation";
  const [tab, setTab] = useState<Tab>("chain");
  const [strategy, setStrategy] = useState<Strategy>("bull_put");
  const [width, setWidth] = useState(2);
  const [legs, setLegs] = useState<Legs | null>(null);
  // Set once the user has clicked a strike; the auto-pick then leaves the
  // legs alone until symbol/expiry/strategy changes.
  const manualRef = useRef(false);

  const chainState = useOptionChain(symbol, enabled);
  const spreads = useSpreads(enabled);
  const badge = modeBadge(mode);
  const { chain, expiries, expiry, setExpiry } = chainState;

  // Auto-pick on a new symbol/expiry/strategy/width, or the first chain.
  useEffect(() => {
    manualRef.current = false;
  }, [symbol, expiry, strategy]);
  useEffect(() => {
    if (!chain) {
      setLegs(null);
      return;
    }
    if (manualRef.current) return;
    setLegs(defaultLegs(strategy, chain, width));
    // Only the inputs of the default pick, not `legs` itself.
  }, [chain, strategy, width]);

  const selection = useMemo(() => selectionOf(strategy, legs), [strategy, legs]);

  const pick = (kind: OptionKind, strike: number) => {
    if (!chain) return;
    manualRef.current = true;
    setLegs((current) => applyPick(strategy, current, chain, kind, strike));
  };

  const resetLegs = () => {
    manualRef.current = false;
    if (chain) setLegs(defaultLegs(strategy, chain, width));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (mode === "live" || !enabled) return;
    const target = e.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;
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
    <div className={`widget options-widget${mode === "live" ? " live-frame" : ""}`} tabIndex={0} onKeyDown={onKeyDown}>
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
                  title={`${e.expiry} · ${e.contract_count} contracts`}
                >
                  {formatExpiry(e.expiry)} <small>{e.dte}d</small>
                </button>
              ))}
              {mode !== "live" && (
                <span className="order-hint">[ ] expiry · 5–9 strategy · + − width</span>
              )}
            </div>
            {chainState.error && <p className="order-rejection">{chainState.error}</p>}
            <div className="options-chain-layout">
              {chain ? (
                <ChainTable chain={chain} selection={selection} pickable={strategyKind(strategy)} onPick={pick} />
              ) : (
                <div className="widget-empty">{chainState.loading ? "Loading chain…" : "No chain."}</div>
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
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
