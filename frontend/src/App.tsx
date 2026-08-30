import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { TradeIdeasWidget } from "./components/ai/TradeIdeasWidget";
import { AlarmsOverlay } from "./components/alarms/AlarmsOverlay";
import { AlarmsToggle } from "./components/alarms/AlarmsToggle";
import { ChartWidget } from "./components/chart/ChartWidget";
import { DashboardGrid } from "./components/layout/DashboardGrid";
import { LayoutModeToggle } from "./components/layout/LayoutModeToggle";
import { ResizablePanels } from "./components/layout/ResizablePanels";
import { ScannerBenchmarkWidget } from "./components/scanner/ScannerBenchmarkWidget";
import { ScannerHistoryWidget } from "./components/scanner/ScannerHistoryWidget";
import { ScannerWidget } from "./components/scanner/ScannerWidget";
import { SimulationToggle } from "./components/trading/SimulationToggle";
import { TradingWidget } from "./components/trading/TradingWidget";
import { WatchlistPanel } from "./components/watchlist/WatchlistPanel";
import { TradingProvider, useTradingContext } from "./context/TradingContext";
import type { ChartFocus } from "./types/screener";
import { useAlarms } from "./hooks/useAlarms";
import { useDashboardLayout, type WidgetId } from "./hooks/useDashboardLayout";
import { useMarketConditions } from "./hooks/useMarketConditions";
import { useMarketSession } from "./hooks/useMarketSession";
import { useTradingMode } from "./hooks/useTradingMode";
import type { TradingMode } from "./api/tradingMode";

const SESSION_LABEL: Record<string, string> = {
  premarket: "Premarket",
  regular: "Market Open",
  afterhours: "After Hours",
  closed: "Closed",
};

const CONDITIONS_LABEL: Record<string, string> = {
  green: "Calm",
  yellow: "Caution",
  red: "Elevated Risk",
};

/** Split out of App so this can call useTradingContext() -- App itself
 * renders <TradingProvider>, so App's own body is not a descendant of it
 * and cannot consume the context. Being a descendant is what lets the
 * Simulation toggle force an immediate refresh() instead of waiting for
 * useTrading's next poll tick (up to POLL_MS) to reflect the switch. */
function AppShell() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  // Where the chart should jump to, set only by clicking a backtest pick.
  const [chartFocus, setChartFocus] = useState<ChartFocus | null>(null);

  // Picking a symbol any other way clears the focus, so the chart returns to
  // its normal live view instead of staying pinned to some earlier pick's
  // timestamp.
  const selectSymbol = useCallback((symbol: string) => {
    setChartFocus(null);
    setSelectedSymbol(symbol);
  }, []);

  const selectPick = useCallback((focus: ChartFocus) => {
    setSelectedSymbol(focus.symbol);
    // A new object identity every time on purpose: clicking the same pick
    // twice should re-centre the chart, which an equal-but-identical focus
    // wouldn't trigger.
    setChartFocus({ ...focus });
  }, []);

  const session = useMarketSession();
  const conditions = useMarketConditions();
  const alarms = useAlarms();
  const dashboardLayout = useDashboardLayout();
  const tradingMode = useTradingMode();
  const { refresh: refreshTrading } = useTradingContext();

  const handleTradingModeChange = useCallback(
    (next: TradingMode) => {
      tradingMode.setMode(next);
      // Without this the switch still works, but positions/orders/account
      // would show the old mode's data until useTrading's next poll tick
      // (up to POLL_MS) -- see the AppShell/App split above.
      refreshTrading();
    },
    [tradingMode, refreshTrading],
  );

  // One set of widget elements, shared by both layouts. Memoized because
  // react-grid-layout re-renders on every pointermove during a drag, and a
  // fresh element identity each time would remount CandleChart's chart
  // instance and restart the analytics widgets' poll intervals.
  const widgets = useMemo<Record<WidgetId, ReactNode>>(
    () => ({
      scanner: (
        <ScannerWidget
          selectedSymbol={selectedSymbol}
          onSelectSymbol={selectSymbol}
          onSelectPick={selectPick}
        />
      ),
      chart: <ChartWidget symbol={selectedSymbol} focus={chartFocus} />,
      ideas: <TradeIdeasWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />,
      benchmark: (
        <ScannerBenchmarkWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
      ),
      history: (
        <ScannerHistoryWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
      ),
      // Owns its own polling hook rather than taking state from here. If that
      // state were lifted into App, this memo would recompute every poll tick
      // and remount CandleChart -- exactly what the comment above guards
      // against. Position lines on the chart (ChartWidget) get the same
      // positions/orders via TradingContext instead, which re-renders its
      // own consumers on a poll tick without touching this memo at all.
      trading: (
        <TradingWidget
          selectedSymbol={selectedSymbol}
          onSelectSymbol={setSelectedSymbol}
          mode={tradingMode.mode}
        />
      ),
      watchlist: (
        <WatchlistPanel selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
      ),
    }),
    // tradingMode.mode is deliberately a dependency, unlike the poll-tick
    // state the comment above guards against: switching modes should
    // remount the trading widget's local state, the same way a symbol
    // change does -- it is a rare, intentional action, not a tick.
    [selectedSymbol, chartFocus, selectSymbol, selectPick, tradingMode.mode],
  );

  return (
    <div className="app-shell">
      <header className="app-header">
          <h1>Stocks in Play</h1>
          <div className="header-actions">
            <a
              className="analytics-link"
              href="/analytics"
              target="_blank"
              rel="noopener noreferrer"
              title="Open Plotly Dash analytics -- scanner heatmap, multi-panel symbol charts, seasonality, and cross-symbol comparison"
            >
              Analytics ↗
            </a>
            {conditions.available && conditions.level && (
              <a
                className="market-conditions-badge"
                data-level={conditions.level}
                href="/analytics/market-conditions"
                target="_blank"
                rel="noopener noreferrer"
                title={`${conditions.reasons?.join(" · ")} -- click for details`}
              >
                <span className="market-conditions-dot" />
                {CONDITIONS_LABEL[conditions.level] ?? conditions.level}
              </a>
            )}
            <span className="session-badge" data-session={session}>
              <span className="session-dot" />
              {SESSION_LABEL[session] ?? session}
            </span>
            <LayoutModeToggle
              mode={dashboardLayout.mode}
              onChange={dashboardLayout.setMode}
              onReset={dashboardLayout.resetLayout}
            />
            <AlarmsToggle
              enabled={alarms.enabled}
              onToggle={alarms.setEnabled}
              activeCount={alarms.alarms.length}
              onClickCount={alarms.openOverlay}
            />
            <SimulationToggle mode={tradingMode.mode} onChange={handleTradingModeChange} />
          </div>
        </header>
        <AlarmsOverlay
          open={alarms.overlayOpen}
          alarms={alarms.alarms}
          onClose={alarms.closeOverlay}
          onSelectSymbol={setSelectedSymbol}
          momentumWindowMinutes={alarms.momentumWindowMinutes}
        />
        <main className="dashboard-main">
          {dashboardLayout.mode === "grid" ? (
            <DashboardGrid
              layout={dashboardLayout.layout}
              onLayoutChange={dashboardLayout.setLayout}
              widgets={widgets}
            />
          ) : (
            <ResizablePanels
              direction="column"
              storageKey="layout:main-rows"
              defaultSizes={[0.65, 0.35]}
              minSizePx={140}
            >
              <ResizablePanels
                direction="row"
                storageKey="layout:top-row"
                defaultSizes={[0.32, 0.44, 0.24]}
                // The trading panel's floor is its own: the order ticket is a
                // narrow form and can give the scanner far more room than a
                // table- or chart-width minimum would allow.
                minSizePx={[220, 220, 150]}
              >
                {widgets.scanner}
                {widgets.chart}
                {/* Trading was oversized for a form at full column height --
                    it shares the column with the watchlist instead, 50/50 by
                    default, each still draggable further via the handle
                    between them. */}
                <ResizablePanels
                  direction="column"
                  storageKey="layout:trading-column"
                  defaultSizes={[0.5, 0.5]}
                  minSizePx={150}
                >
                  {widgets.trading}
                  {widgets.watchlist}
                </ResizablePanels>
              </ResizablePanels>
              <ResizablePanels
                direction="row"
                storageKey="layout:bottom-row"
                defaultSizes={[0.34, 0.33, 0.33]}
                minSizePx={220}
              >
                {widgets.ideas}
                {widgets.benchmark}
                {widgets.history}
              </ResizablePanels>
            </ResizablePanels>
          )}
        </main>
      </div>
  );
}

export default function App() {
  return (
    <TradingProvider>
      <AppShell />
    </TradingProvider>
  );
}
