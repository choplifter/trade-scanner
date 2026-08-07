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
        <span className="session-badge" data-session={session}>
          <span className="session-dot" />
          {SESSION_LABEL[session] ?? session}
        </span>
      </header>
      <main className="dashboard-main">
        <ResizablePanels
          direction="column"
          storageKey="layout:main-rows"
          defaultSizes={[0.35, 0.3, 0.35]}
          minSizePx={140}
        >
          <ResizablePanels
            direction="row"
            storageKey="layout:scanner-columns"
            defaultSizes={[0.5, 0.5]}
            minSizePx={220}
          >
            <ScannerWidget
              scanner="gainers"
              title="Market Gainers"
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
            />
            <ScannerWidget
              scanner="premarket_gainers"
              title="Premarket Gainers"
              selectedSymbol={selectedSymbol}
              onSelectSymbol={setSelectedSymbol}
            />
          </ResizablePanels>
          <TradeIdeasWidget selectedSymbol={selectedSymbol} onSelectSymbol={setSelectedSymbol} />
          <ChartWidget symbol={selectedSymbol} />
        </ResizablePanels>
      </main>
    </div>
  );
}
