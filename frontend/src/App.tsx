import { useState } from "react";

import { TradeIdeasWidget } from "./components/ai/TradeIdeasWidget";
import { AlarmsOverlay } from "./components/alarms/AlarmsOverlay";
import { AlarmsToggle } from "./components/alarms/AlarmsToggle";
import { ChartWidget } from "./components/chart/ChartWidget";
import { ResizablePanels } from "./components/layout/ResizablePanels";
import { ScannerBenchmarkWidget } from "./components/scanner/ScannerBenchmarkWidget";
import { ScannerHistoryWidget } from "./components/scanner/ScannerHistoryWidget";
import { ScannerWidget } from "./components/scanner/ScannerWidget";
import { useAlarms } from "./hooks/useAlarms";
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
            <ScannerWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
            <ChartWidget symbol={selectedSymbol} />
          </ResizablePanels>
          <ResizablePanels
            direction="row"
            storageKey="layout:bottom-row"
            defaultSizes={[0.34, 0.33, 0.33]}
            minSizePx={220}
          >
            <TradeIdeasWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
            <ScannerBenchmarkWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
            <ScannerHistoryWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
          </ResizablePanels>
        </ResizablePanels>
      </main>
    </div>
  );
}
