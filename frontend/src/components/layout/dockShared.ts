import { createContext } from "react";
import type { ReactNode } from "react";

import type { DockviewApi } from "dockview-react";

import { WIDGET_IDS, type WidgetId } from "../../hooks/useDashboardLayout";

/** Panels only the dock offers: parts of the Options widget that can sit
 * where the work needs them (see components/options/OptionsPanels.tsx).
 * Kept out of WidgetId on purpose -- the grid requires every WidgetId in
 * its stored layout exactly once, and panels mode has a fixed place for
 * each, so a new WidgetId would cost every saved grid. */
export type DockOnlyWidgetId = "options_risk" | "options_screener" | "options_spreads" | "options_record";

export const DOCK_ONLY_WIDGET_IDS: readonly DockOnlyWidgetId[] = [
  "options_risk",
  "options_screener",
  "options_spreads",
  "options_record",
];

export type DockWidgetId = WidgetId | DockOnlyWidgetId;

export const DOCK_WIDGET_IDS: readonly DockWidgetId[] = [...WIDGET_IDS, ...DOCK_ONLY_WIDGET_IDS];

/** What every dock panel carries. Singletons (the default layout's
 * widgets) have `id === widgetId` and no `copy`; a copy opened from a
 * tab's context menu has id `${widgetId}#${n}` and, for the chart, its
 * own pinned `symbol`. */
export interface PanelParams {
  widgetId: DockWidgetId;
  copy?: boolean;
  symbol?: string;
}

export const WIDGET_TITLES: Record<DockWidgetId, string> = {
  scanner: "Scanner",
  chart: "Chart",
  ideas: "AI Trade Ideas",
  benchmark: "Scanner vs SPY",
  history: "Scanner Match History",
  trading: "Trading",
  watchlist: "Watchlist",
  replay: "History Replay",
  news_feed: "News Feed",
  symbol_info: "Symbol Info",
  gex_plan: "GEX Plan",
  trade_journal: "Trading Journal",
  options: "Options",
  earnings: "Earnings",
  options_risk: "Risk chart",
  options_screener: "Options screener",
  options_spreads: "Open spreads",
  options_record: "Prediction record",
};

export interface DockContextValue {
  /** Widget elements by stable id -- the same memoized record the other
   * layouts render from. */
  widgets: Record<DockWidgetId, ReactNode>;
  /** The dashboard's current (underlying) symbol: what a new chart copy is
   * pinned to when it opens. */
  selectedSymbol: string | null;
}

/** Dockview panels are managed by its own imperative model, not by React
 * children -- a panel's `params` are fixed at `addPanel()` time and don't
 * update on their own when `widgets` changes (e.g. a new selectedSymbol).
 * Rather than pushing updates through Dockview's imperative
 * `panel.api.updateParameters()` on every App re-render, each panel's
 * content component reads the *current* record from context -- ordinary
 * React re-rendering, same as every other consumer of `widgets`. */
export const DockContext = createContext<DockContextValue | null>(null);

/** The smallest free `${widgetId}#${n}` (n from 2). */
export function nextCopyId(api: DockviewApi, widgetId: DockWidgetId): string {
  const taken = new Set(api.panels.map((p) => p.id));
  let n = 2;
  while (taken.has(`${widgetId}#${n}`)) n += 1;
  return `${widgetId}#${n}`;
}
