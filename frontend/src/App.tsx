import { useState } from "react";

import { TradeIdeasWidget } from "./components/ai/TradeIdeasWidget";
import { ChartWidget } from "./components/chart/ChartWidget";
import { ResizablePanels } from "./components/layout/ResizablePanels";
import { ScannerWidget } from "./components/scanner/ScannerWidget";
import { useMarketSession } from "./hooks/useMarketSession";

const SESSION_LABEL: Record<string, string> = {
  premarket: "Premarket",
  regular: "Market Open",
  afterhours: "After Hours",
  closed: "Closed",
};

export default function App() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const session = useMarketSession();

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
          <span className="session-badge" data-session={session}>
            <span className="session-dot" />
            {SESSION_LABEL[session] ?? session}
          </span>
        </div>
      </header>
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
          <TradeIdeasWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
        </ResizablePanels>
      </main>
    </div>
  );
}
