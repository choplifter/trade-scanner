import { useEffect, useMemo, useState } from "react";

import { getScreenerFields, getScreenerPresets } from "../../api/http";
import { useScannerFeed } from "../../hooks/useScannerFeed";
import { useScreenFeed } from "../../hooks/useScreenFeed";
import type { FieldSpec, Preset, Screen } from "../../types/screener";
import { ScannerFilterBar } from "../screener/ScannerFilterBar";
import { ScannerHeatmap } from "./ScannerHeatmap";
import { ScannerTable } from "./ScannerTable";

interface ScannerWidgetProps {
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

/**
 * The scanner and the screener are one widget: the old fixed tabs are
 * presets, and every preset is an ordinary screen you can then edit.
 *
 * "Premarket Gainers" is the exception and stays a named-view subscription.
 * It isn't a question about the current rows at all -- it's a snapshot of the
 * gap frozen at 09:30 (see ScannerEngine._premarket_snapshot), so there is no
 * filter that expresses it. The widget therefore drives two feeds and shows
 * whichever the active tab needs; the filter bar hides itself on that tab
 * rather than offering controls that would do nothing.
 */
const FROZEN_VIEW = "premarket_gainers";

const VIEW_MODES = [
  { key: "table", label: "Table" },
  { key: "heatmap", label: "Heatmap" },
] as const;

const DEFAULT_SCREEN: Screen = {
  filters: [],
  sort_by: "pct_change",
  descending: true,
  limit: 50,
};

export function ScannerWidget({ selectedSymbol, onSelectSymbol }: ScannerWidgetProps) {
  const [fields, setFields] = useState<FieldSpec[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [screen, setScreen] = useState<Screen>(DEFAULT_SCREEN);
  const [viewMode, setViewMode] = useState<(typeof VIEW_MODES)[number]["key"]>("table");
  const [showFilters, setShowFilters] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getScreenerFields(), getScreenerPresets()])
      .then(([fieldsRes, presetsRes]) => {
        if (cancelled) return;
        setFields(fieldsRes.fields);
        setPresets(presetsRes.presets);
        // Open on the first preset so the widget behaves like the old
        // scanner did -- showing gainers immediately, not an empty table.
        const first = presetsRes.presets[0];
        if (first) {
          setActivePreset(first.name);
          setScreen(first.screen);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load screener fields");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const frozenActive = activePreset === FROZEN_VIEW;
  // Both hooks always run -- hooks can't be conditional -- but each is fed
  // null when its tab isn't active, so only one feed is actually subscribed.
  const frozenFeed = useScannerFeed(frozenActive ? FROZEN_VIEW : null);
  const screenFeed = useScreenFeed(frozenActive ? null : screen);

  const rows = frozenActive ? frozenFeed.rows : screenFeed.rows;
  const loading = frozenActive ? frozenFeed.loading : screenFeed.loading;
  const isLatestSession = frozenActive ? frozenFeed.isLatestSession : screenFeed.isLatestSession;

  const tabs = useMemo(
    () => [...presets.map((p) => ({ key: p.name, label: p.label })), { key: FROZEN_VIEW, label: "Premarket Gainers" }],
    [presets],
  );

  const selectTab = (key: string) => {
    setActivePreset(key);
    const preset = presets.find((p) => p.name === key);
    if (preset) setScreen(preset.screen);
  };

  const updateScreen = (next: Screen) => {
    setScreen(next);
    // Editing a preset's filters makes it no longer that preset. Clearing the
    // highlight is what stops the UI claiming you're looking at "Top Gainers"
    // when you've changed what it asks.
    const preset = presets.find((p) => p.name === activePreset);
    if (preset && JSON.stringify(preset.screen) !== JSON.stringify(next)) {
      setActivePreset(null);
    }
  };

  return (
    <div className="widget">
      <div className="widget-header scanner-header">
        <div className="scanner-tabs">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={activePreset === tab.key ? "tab active" : "tab"}
              aria-pressed={activePreset === tab.key}
              onClick={() => selectTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
          {activePreset === null && <span className="tab custom-tab">Custom</span>}
        </div>
        <div className="scanner-header-actions">
          {!frozenActive && (
            <button type="button" onClick={() => setShowFilters((v) => !v)}>
              {showFilters ? "Hide filters" : "Filters"}
              {screen.filters.length > 0 ? ` (${screen.filters.length})` : ""}
            </button>
          )}
          {VIEW_MODES.map((mode) => (
            <button
              key={mode.key}
              type="button"
              className={viewMode === mode.key ? "tab active" : "tab"}
              aria-pressed={viewMode === mode.key}
              onClick={() => setViewMode(mode.key)}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="widget-error">{error}</p>}

      {showFilters && !frozenActive && (
        <ScannerFilterBar fields={fields} screen={screen} onChange={updateScreen} />
      )}

      {!loading && (
        <p className="screener-summary">
          {frozenActive
            ? `Gap frozen at the 09:30 open — ${rows.length} symbols`
            : `${screenFeed.totalMatched} of ${screenFeed.tradableSize} tradable matched (${screenFeed.universeSize} in universe); showing ${rows.length}`}
          {isLatestSession ? " — last completed session" : ""}
        </p>
      )}

      {loading ? (
        <p className="widget-empty">Loading…</p>
      ) : viewMode === "table" ? (
        <ScannerTable rows={rows} selectedSymbol={selectedSymbol} onSelectSymbol={onSelectSymbol} />
      ) : (
        <ScannerHeatmap rows={rows} selectedSymbol={selectedSymbol} onSelectSymbol={onSelectSymbol} />
      )}
    </div>
  );
}
