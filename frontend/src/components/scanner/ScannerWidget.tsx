import { useState } from "react";

import { useScannerFeed } from "../../hooks/useScannerFeed";
import { ScannerHeatmap } from "./ScannerHeatmap";
import { ScannerTable } from "./ScannerTable";

interface ScannerWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

const TABS = [
  { key: "gainers", label: "Market Gainers" },
  { key: "premarket_gainers", label: "Premarket Gainers" },
  { key: "losers", label: "Losers" },
  { key: "most_active", label: "Most Active" },
] as const;

const VIEW_MODES = [
  { key: "table", label: "Table" },
  { key: "heatmap", label: "Heatmap" },
] as const;

export function ScannerWidget({ selectedSymbol, onSelectSymbol }: ScannerWidgetProps) {
  const [activeKey, setActiveKey] = useState<(typeof TABS)[number]["key"]>(TABS[0].key);
  const [viewMode, setViewMode] = useState<(typeof VIEW_MODES)[number]["key"]>("table");
  const { rows, loading, isLatestSession } = useScannerFeed(activeKey);

  return (
    <div className="widget">
      <div className="widget-header">
        <div className="timeframe-selector" role="group" aria-label="Scanner">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className="timeframe-button"
              aria-pressed={activeKey === tab.key}
              onClick={() => setActiveKey(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="header-actions">
          <div className="timeframe-selector" role="group" aria-label="View">
            {VIEW_MODES.map((mode) => (
              <button
                key={mode.key}
                type="button"
                className="timeframe-button"
                aria-pressed={viewMode === mode.key}
                onClick={() => setViewMode(mode.key)}
              >
                {mode.label}
              </button>
            ))}
          </div>
          {isLatestSession && rows.length > 0 && (
            <span
              className="stale-badge"
              title="Markets are closed -- showing the most recently completed session's real gap, not a live scan."
            >
              LAST SESSION
            </span>
          )}
          <span className="widget-count">{rows.length}</span>
        </div>
      </div>
      <div className="widget-body">
        {loading && rows.length === 0 ? (
          <div className="widget-empty">Loading…</div>
        ) : viewMode === "table" ? (
          <ScannerTable
            rows={rows}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
          />
        ) : (
          <ScannerHeatmap
            rows={rows}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
          />
        )}
      </div>
    </div>
  );
}
