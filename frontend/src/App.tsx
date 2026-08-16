import { useMemo, useState } from "react";
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
import { useAlarms } from "./hooks/useAlarms";
import { useDashboardLayout, type WidgetId } from "./hooks/useDashboardLayout";
import { useMarketConditions } from "./hooks/useMarketConditions";
import { useMarketSession } from "./hooks/useMarketSession";

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

export default function App() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const session = useMarketSession();
  const conditions = useMarketConditions();
  const alarms = useAlarms();
  const dashboardLayout = useDashboardLayout();

  // One set of widget elements, shared by both layouts. Memoized because
  // react-grid-layout re-renders on every pointermove during a drag, and a
  // fresh element identity each time would remount CandleChart's chart
  // instance and restart the analytics widgets' poll intervals.
  const widgets = useMemo<Record<WidgetId, ReactNode>>(
    () => ({
      scanner: <ScannerWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />,
      chart: <ChartWidget symbol={selectedSymbol} />,
      ideas: <TradeIdeasWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />,
      benchmark: (
        <ScannerBenchmarkWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
      ),
      history: (
        <ScannerHistoryWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
      ),
    }),
    [selectedSymbol],
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
        </div>
      </header>
      <AlarmsOverlay
        open={alarms.overlayOpen}
        alarms={alarms.alarms}
        onClose={alarms.closeOverlay}
        onSelectSymbol={setSelectedSymbol}
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
              defaultSizes={[0.45, 0.55]}
              minSizePx={220}
            >
              {widgets.scanner}
              {widgets.chart}
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
