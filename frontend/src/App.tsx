import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import type { ReactNode } from "react";

import { subscribeOpenSettings, type SettingsTab } from "./api/settingsDialog";

import { TradeIdeasWidget } from "./components/ai/TradeIdeasWidget";
import { AlarmsOverlay } from "./components/alarms/AlarmsOverlay";
import { AlarmsToggle } from "./components/alarms/AlarmsToggle";
import { LoginPage } from "./components/auth/LoginPage";
import { ChartWidget } from "./components/chart/ChartWidget";
import { SymbolInfoWidget } from "./components/chart/SymbolInfoWidget";
import { GexPlanWidget } from "./components/gex/GexPlanWidget";
import { chartSymbolOf, parseOcc } from "./utils/occ";
import { OptionsWidget } from "./components/options/OptionsWidget";
import { DashboardGrid } from "./components/layout/DashboardGrid";
import { DockviewDashboard } from "./components/layout/DockviewDashboard";
import { LayoutModeToggle } from "./components/layout/LayoutModeToggle";
import { ResizablePanels } from "./components/layout/ResizablePanels";
import { NewsFeedWidget } from "./components/newsFeed/NewsFeedWidget";
import { ReplayPanel } from "./components/replay/ReplayPanel";
import { ScannerBenchmarkWidget } from "./components/scanner/ScannerBenchmarkWidget";
import { ScannerHistoryWidget } from "./components/scanner/ScannerHistoryWidget";
import { ScannerWidget } from "./components/scanner/ScannerWidget";
import { TradingModeSwitch } from "./components/trading/TradingModeSwitch";
import { SettingsDialog } from "./components/settings/SettingsDialog";
import "./api/settings";
import { TradeJournalWidget } from "./components/trading/TradeJournalWidget";
import { TradingWidget } from "./components/trading/TradingWidget";
import { WatchlistPanel } from "./components/watchlist/WatchlistPanel";
import { SymbolInfoProvider } from "./context/SymbolInfoContext";
import { SpreadLevelsProvider } from "./context/SpreadLevelsContext";
import { TradingProvider, useTradingContext } from "./context/TradingContext";
import type { ChartFocus } from "./types/screener";
import type { User } from "./api/auth";
import { useAlarms } from "./hooks/useAlarms";
import { useAuth } from "./hooks/useAuth";
import { useDashboardLayout, type WidgetId } from "./hooks/useDashboardLayout";
import { useMarketConditions } from "./hooks/useMarketConditions";
import { useMarketSession } from "./hooks/useMarketSession";
import { useReplaySession } from "./hooks/useReplaySession";
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
interface AppShellProps {
  user: User;
  onLogout: () => void;
}

function AppShell({ user, onLogout }: AppShellProps) {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  // The Settings dialog: closed until the header's button opens it, or a
  // widget asks for a tab (the "connect your broker" panels).
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null);
  useEffect(
    () =>
      subscribeOpenSettings((tab) => {
        setSettingsTab(tab);
        setSettingsOpen(true);
      }),
    [],
  );
  // Set by DockviewDashboard once its api is ready; see its resetRef prop.
  const dockResetRef = useRef<(() => void) | null>(null);
  // Where the chart should jump to, set only by clicking a backtest pick.
  const [chartFocus, setChartFocus] = useState<ChartFocus | null>(null);

  // Picking a symbol any other way clears the focus, so the chart returns to
  // its normal live view instead of staying pinned to some earlier pick's
  // timestamp.
  const selectSymbol = useCallback((symbol: string) => {
    setChartFocus(null);
    setSelectedSymbol(symbol);
  }, []);

  // The chart may show an option contract's premium; every other widget
  // (ticket, info, news, chain...) works on the underlying stock.
  const underlying = selectedSymbol ? chartSymbolOf(selectedSymbol) : null;
  const focusContract = useMemo(() => (selectedSymbol ? parseOcc(selectedSymbol) : null), [selectedSymbol]);

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
  // Drives panels-mode's idle-vs-active row split below -- see the
  // ResizablePanels branch in the render. Grid mode needs no equivalent:
  // ReplayPanel's own .replay-collapsed class (styles.css) handles its
  // content-level collapse regardless of layout mode, this only decides
  // whether panels mode gives the row its own resizable slot back.
  const replaySession = useReplaySession();
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
          selectedSymbol={underlying}
          onSelectSymbol={selectSymbol}
          onSelectPick={selectPick}
        />
      ),
      chart: (
        <ChartWidget
          symbol={selectedSymbol}
          focus={chartFocus}
          onClearFocus={() => setChartFocus(null)}
          onSelectSymbol={selectSymbol}
        />
      ),
      symbol_info: <SymbolInfoWidget symbol={underlying} />,
      ideas: <TradeIdeasWidget selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />,
      benchmark: (
        <ScannerBenchmarkWidget selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />
      ),
      history: (
        <ScannerHistoryWidget selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />
      ),
      // Owns its own polling hook rather than taking state from here. If that
      // state were lifted into App, this memo would recompute every poll tick
      // and remount CandleChart -- exactly what the comment above guards
      // against. Position lines on the chart (ChartWidget) get the same
      // positions/orders via TradingContext instead, which re-renders its
      // own consumers on a poll tick without touching this memo at all.
      trading: (
        <TradingWidget
          selectedSymbol={underlying}
          onSelectSymbol={setSelectedSymbol}
          mode={tradingMode.mode}
        />
      ),
      watchlist: (
        <WatchlistPanel selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />
      ),
      // key: harmless as a plain widgets.replay reference (React strips it
      // from props either way), but required where App's panels-mode
      // layout places this element inside an array alongside topAndBottomRows
      // instead of as its own literal JSX child -- see that array's build site.
      replay: (
        <ReplayPanel key="replay" selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />
      ),
      // Same key requirement as replay above -- also placed inside a
      // constructed children array in panels mode, not as a literal JSX
      // child.
      news_feed: (
        <NewsFeedWidget key="news_feed" selectedSymbol={underlying} onSelectSymbol={setSelectedSymbol} />
      ),
      // Same key requirement as replay/news_feed above -- placed outside
      // the resizable splits entirely in panels mode (see the
      // dashboard-active-column/dashboard-idle-column render sites below).
      gex_plan: <GexPlanWidget key="gex_plan" symbol={underlying} />,
      // Same key requirement as gex_plan above -- also placed outside the
      // resizable splits in panels mode.
      trade_journal: <TradeJournalWidget key="trade_journal" onSelectPick={selectPick} />,
      // Options chain + spread ticket + open spreads. Same key requirement;
      // the mode dependency below remounts it on a mode switch like the
      // trading widget.
      options: (
        <OptionsWidget
          key="options"
          symbol={underlying}
          mode={tradingMode.mode}
          onSelectSymbol={selectSymbol}
          focusContract={focusContract}
        />
      ),
    }),
    // tradingMode.mode is deliberately a dependency, unlike the poll-tick
    // state the comment above guards against: switching modes should
    // remount the trading widget's local state, the same way a symbol
    // change does -- it is a rare, intentional action, not a tick.
    [selectedSymbol, underlying, focusContract, chartFocus, selectSymbol, selectPick, tradingMode.mode],
  );

  // Shared between the panels-mode active and idle layouts below -- only
  // whether replay gets a resizable third row differs between them, not
  // this part. An array, not a fragment: ResizablePanels treats each item
  // of its `children` array as one resizable panel (see its `children.map`
  // in ResizablePanels.tsx), so this has to end up as two flat entries in
  // the parent ResizablePanels' children array, not one combined node --
  // each needs its own `key` for the same reason any array of elements does.
  const topAndBottomRows: ReactNode[] = [
    <ResizablePanels
      key="top-row"
      direction="row"
      storageKey="layout:top-row"
      defaultSizes={[0.32, 0.44, 0.24]}
      // The trading panel's floor is its own: the order ticket is a
      // narrow form and can give the scanner far more room than a
      // table- or chart-width minimum would allow.
      minSizePx={[220, 220, 150]}
    >
      {widgets.scanner}
      {/* Symbol info and news feed sit directly under their own chart --
          click a headline, the chart right above updates to that symbol,
          no need to look elsewhere on the page. Symbol info used to be
          embedded inside ChartWidget itself; it's a stacked panel here
          instead now, same as news_feed, so the chart's own box is
          candles-only. */}
      <ResizablePanels
        direction="column"
        storageKey="layout:chart-column"
        defaultSizes={[0.55, 0.25, 0.2]}
        minSizePx={120}
      >
        {widgets.chart}
        {widgets.symbol_info}
        {widgets.news_feed}
      </ResizablePanels>
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
    </ResizablePanels>,
    <ResizablePanels
      key="bottom-row"
      direction="row"
      storageKey="layout:bottom-row"
      defaultSizes={[0.34, 0.33, 0.33]}
      minSizePx={220}
    >
      {widgets.ideas}
      {widgets.benchmark}
      {widgets.history}
    </ResizablePanels>,
  ];

  return (
    <SymbolInfoProvider symbol={underlying}>
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
              onReset={() => {
                if (dashboardLayout.mode === "dock") dockResetRef.current?.();
                else dashboardLayout.resetLayout();
              }}
            />
            <AlarmsToggle
              enabled={alarms.enabled}
              onToggle={alarms.setEnabled}
              activeCount={alarms.alarms.length}
              onClickCount={alarms.openOverlay}
            />
            <TradingModeSwitch mode={tradingMode.mode} onChange={handleTradingModeChange} />
            <button
              type="button"
              className="settings-toggle"
              onClick={() => setSettingsOpen(true)}
              title="Settings: chart colours, appearance, chart defaults, number format, hotkeys"
            >
              ⚙ Settings
            </button>
            <SettingsDialog
              open={settingsOpen}
              initialTab={settingsTab}
              isAdmin={!!user.is_admin}
              currentUserId={user.id}
              onClose={() => {
                setSettingsOpen(false);
                setSettingsTab(null);
              }}
            />
            <span className="logout-link">
              {user.display_name} ·{" "}
              <button type="button" className="row-action" onClick={onLogout}>
                Logout
              </button>
            </span>
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
          ) : dashboardLayout.mode === "dock" ? (
            <DockviewDashboard widgets={widgets} selectedSymbol={underlying} resetRef={dockResetRef} />
          ) : replaySession ? (
            // A session is running -- replay gets its own resizable third
            // row, same as it always has. news_feed no longer lives here --
            // it's nested under widgets.chart instead (see topAndBottomRows'
            // top-row). One array expression as the sole child (not
            // {topAndBottomRows}{widgets.replay} as two separate ones) --
            // see topAndBottomRows' own comment for why: two expressions
            // here would nest the 2-element array inside a 2-element
            // children array ([[top,bottom], replay], length 2), not
            // flatten to the 3 panels defaultSizes expects.
            <div className="dashboard-active-column">
              <ResizablePanels
                direction="column"
                storageKey="layout:main-rows"
                defaultSizes={[0.55, 0.25, 0.2]}
                minSizePx={140}
              >
                {[...topAndBottomRows, widgets.replay]}
              </ResizablePanels>
              {widgets.gex_plan}
              {widgets.trade_journal}
              {widgets.options}
            </div>
          ) : (
            // No session -- replay collapses to its own content-sized strip
            // (see ReplayPanel's .replay-collapsed) outside the resizable
            // split entirely, so the two real rows get that row's screen
            // space back instead of it sitting empty inside a fixed-height
            // slot. A distinct storageKey from the active case above: the
            // two ResizablePanels here have different child counts (2 vs.
            // 3), and ResizablePanels' own loadSizes already falls back to
            // defaultSizes on a length mismatch -- sharing one key would
            // otherwise have switching between idle and active silently
            // clobber whichever arrangement wasn't currently on screen.
            <div className="dashboard-idle-column">
              <ResizablePanels
                direction="column"
                storageKey="layout:main-rows-idle"
                defaultSizes={[0.65, 0.35]}
                minSizePx={140}
              >
                {topAndBottomRows}
              </ResizablePanels>
              {widgets.replay}
              {widgets.gex_plan}
              {widgets.trade_journal}
              {widgets.options}
            </div>
          )}
        </main>
      </div>
    </SymbolInfoProvider>
  );
}

export default function App() {
  const auth = useAuth();

  // Blank rather than a spinner for this one beat -- it's a single getMe()
  // round trip, and a flash of "logged out" before it resolves would be
  // worse than a brief blank frame.
  if (auth.loading) return null;

  if (!auth.user) {
    return (
      <LoginPage
        onLogin={async (username, password) => {
          await auth.login(username, password);
        }}
      />
    );
  }

  return (
    <TradingProvider>
      <SpreadLevelsProvider>
      <AppShell user={auth.user} onLogout={() => void auth.logout()} />
      </SpreadLevelsProvider>
    </TradingProvider>
  );
}
