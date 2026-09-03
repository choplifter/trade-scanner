import { createContext } from "react";
import type { ReactNode } from "react";

import type { DockviewApi } from "dockview-react";

import type { WidgetId } from "../../hooks/useDashboardLayout";

/** What every dock panel carries. Singletons (the default layout's
 * widgets) have `id === widgetId` and no `copy`; a copy opened from a
 * tab's context menu has id `${widgetId}#${n}` and, for the chart, its
 * own pinned `symbol`. */
export interface PanelParams {
  widgetId: WidgetId;
  copy?: boolean;
  symbol?: string;
}

export const WIDGET_TITLES: Record<WidgetId, string> = {
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
};

export interface DockContextValue {
  /** Widget elements by stable id -- the same memoized record the other
   * layouts render from. */
  widgets: Record<WidgetId, ReactNode>;
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
export function nextCopyId(api: DockviewApi, widgetId: WidgetId): string {
  const taken = new Set(api.panels.map((p) => p.id));
  let n = 2;
  while (taken.has(`${widgetId}#${n}`)) n += 1;
  return `${widgetId}#${n}`;
}
