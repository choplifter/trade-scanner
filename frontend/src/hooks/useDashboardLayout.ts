import { useCallback, useEffect, useRef, useState } from "react";

import type { Layout } from "react-grid-layout";

/** Which layout the dashboard is rendering: the fixed nested splitters
 * (ResizablePanels) or the freely repositionable grid (DashboardGrid). */
export type LayoutMode = "panels" | "grid";

/** Stable per-widget identity. These strings are the react-grid-layout item
 * ids *and* the React keys for the grid cells, which is the whole point:
 * ResizablePanels keys its children by array index, so reordering there
 * would remount subtrees -- destroying CandleChart's chart instance, every
 * widget's local useState, and restarting the three analytics widgets'
 * poll intervals. Keying by id means dragging only moves a cell. */
export type WidgetId = "scanner" | "chart" | "ideas" | "benchmark" | "history" | "trading" | "watchlist";

/** Render order of grid cells. Deliberately constant and independent of
 * `layout` -- position comes from the layout item's x/y, never from DOM
 * order, so a drag never reorders children. */
export const WIDGET_IDS: readonly WidgetId[] = [
  "scanner",
  "chart",
  "ideas",
  "benchmark",
  "history",
  "trading",
  "watchlist",
];

export const GRID_COLS = 12;
/** Rows the viewport is divided into. rowHeight is derived from the measured
 * container height (see DashboardGrid) rather than hardcoded, because
 * react-grid-layout's autoSize makes container height content-driven
 * (max(y+h) x rowHeight) while .app-shell is a fixed 100vh. */
export const GRID_ROWS = 12;
export const GRID_MARGIN = 8;

/** Mirrors today's ResizablePanels arrangement (App.tsx): scanner + chart
 * across the top at 65% height / 45-55% width, trading and watchlist
 * stacked in the same right-hand column, the three analytics widgets across
 * the bottom -- so switching into grid mode looks identical until you
 * actually drag something. minW/minH approximate the old minSizePx clamps.
 * `chart` gets a taller minH because .symbol-info-panel reserves up to 220px
 * inside ChartWidget, which would otherwise squeeze the canvas to nothing.
 *
 * The grid was already full at 12x12 when "trading" was added, so it took
 * width from the top row rather than a sixth row; "watchlist" later shared
 * that same column instead of adding a fourth row, splitting it h:4/h:4
 * with trading, which was oversized for a form at h:8 anyway. */
export const DEFAULT_LAYOUT: Layout = [
  { i: "scanner", x: 0, y: 0, w: 4, h: 8, minW: 3, minH: 3 },
  { i: "chart", x: 4, y: 0, w: 5, h: 8, minW: 3, minH: 4 },
  // minW 2, not 3: the order ticket is a narrow form, and its floor is what
  // caps how wide the scanner and chart can go in the top row.
  { i: "trading", x: 9, y: 0, w: 3, h: 4, minW: 2, minH: 3 },
  { i: "watchlist", x: 9, y: 4, w: 3, h: 4, minW: 2, minH: 2 },
  { i: "ideas", x: 0, y: 8, w: 4, h: 4, minW: 2, minH: 2 },
  { i: "benchmark", x: 4, y: 8, w: 4, h: 4, minW: 2, minH: 2 },
  { i: "history", x: 8, y: 8, w: 4, h: 4, minW: 2, minH: 2 },
];

const LAYOUT_STORAGE_KEY = "layout:grid";
const MODE_STORAGE_KEY = "layout:mode";
/** Bump when WIDGET_IDS or the grid geometry changes incompatibly; a stored
 * layout with a different version is discarded rather than migrated. */
// 2: briefly added a separate "screener" widget.
// 3: folded it back into "scanner" -- they're one thing now, so the id is
// gone again. isValidLayout requires every id exactly once, so both the v1
// and v2 shapes are incompatible; bumping discards a stored layout rather
// than leaving a widget invisible or an orphan id in the grid.
// 4: added "trading". isValidLayout's id check would already reject a v3
// layout on its own, so this bump is belt-and-braces -- but it keeps the
// version honest about the geometry change to the top row.
// 5: added "watchlist", reflowing the bottom row from 3 widgets to 4.
// 6: moved "watchlist" into the trading column (stacked, sharing its height)
// instead of the bottom row, which goes back to 3 widgets at w:4.
const LAYOUT_VERSION = 6;
/** react-grid-layout fires onLayoutChange on every pointermove during a
 * drag, and this payload is far larger than ResizablePanels' size array --
 * so unlike that component, don't write synchronously on every change. */
const WRITE_DEBOUNCE_MS = 200;

interface StoredLayout {
  version: number;
  layout: Layout;
}

function isValidLayout(value: unknown): value is Layout {
  if (!Array.isArray(value) || value.length !== WIDGET_IDS.length) return false;
  const seen = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object") return false;
    const { i, x, y, w, h } = item as Record<string, unknown>;
    if (typeof i !== "string" || !WIDGET_IDS.includes(i as WidgetId)) return false;
    if (![x, y, w, h].every((n) => typeof n === "number" && Number.isFinite(n))) return false;
    seen.add(i);
  }
  // Every widget must be present exactly once -- a layout missing an id
  // would silently drop that widget off the dashboard.
  return seen.size === WIDGET_IDS.length;
}

/** Positions belong to the user; constraints belong to the code. A stored
 * layout carries whatever minW/minH were current when it was saved (grid
 * onLayoutChange persists full items), so without this a loosened floor
 * would never reach anyone with a saved layout -- their old clamp rides
 * along in localStorage forever. */
function withCodeOwnedConstraints(layout: Layout): Layout {
  const defaults = new Map(DEFAULT_LAYOUT.map((item) => [item.i, item]));
  return layout.map((item) => {
    const base = defaults.get(item.i);
    return base ? { ...item, minW: base.minW, minH: base.minH } : item;
  });
}

function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const parsed = JSON.parse(raw) as Partial<StoredLayout>;
    if (parsed?.version === LAYOUT_VERSION && isValidLayout(parsed.layout)) {
      return withCodeOwnedConstraints(parsed.layout);
    }
  } catch {
    // Corrupt/foreign/stale localStorage value -- fall back to defaults
    // rather than crash the dashboard, same as ResizablePanels' loadSizes.
  }
  return DEFAULT_LAYOUT;
}

function loadMode(): LayoutMode {
  try {
    return localStorage.getItem(MODE_STORAGE_KEY) === "grid" ? "grid" : "panels";
  } catch {
    return "panels";
  }
}

export interface DashboardLayoutState {
  mode: LayoutMode;
  setMode: (mode: LayoutMode) => void;
  layout: Layout;
  setLayout: (layout: Layout) => void;
  resetLayout: () => void;
}

/**
 * Layout mode + grid arrangement, persisted to localStorage. Follows the
 * hand-rolled convention already used by useAlarms and ResizablePanels
 * (module-level `namespace:thing` keys, a load function that swallows errors
 * and returns defaults, read once via a lazy useState initializer) rather
 * than introducing a store -- with two deliberate improvements over
 * ResizablePanels: writes are wrapped in try/catch so private-browsing mode
 * can't throw, and the stored value carries a version.
 */
export function useDashboardLayout(): DashboardLayoutState {
  const [mode, setModeState] = useState<LayoutMode>(loadMode);
  const [layout, setLayoutState] = useState<Layout>(loadLayout);
  const writeTimer = useRef<number | null>(null);
  const pending = useRef<Layout | null>(null);

  const flush = useCallback(() => {
    if (writeTimer.current !== null) {
      clearTimeout(writeTimer.current);
      writeTimer.current = null;
    }
    const next = pending.current;
    if (!next) return;
    pending.current = null;
    try {
      localStorage.setItem(
        LAYOUT_STORAGE_KEY,
        JSON.stringify({ version: LAYOUT_VERSION, layout: next } satisfies StoredLayout),
      );
    } catch {
      // Storage disabled -- the layout still works for this session.
    }
  }, []);

  // Don't lose the last drag if the tab closes inside the debounce window.
  useEffect(() => flush, [flush]);

  const setLayout = useCallback(
    (next: Layout) => {
      setLayoutState(next);
      pending.current = next;
      if (writeTimer.current !== null) clearTimeout(writeTimer.current);
      writeTimer.current = window.setTimeout(flush, WRITE_DEBOUNCE_MS);
    },
    [flush],
  );

  const setMode = useCallback((next: LayoutMode) => {
    setModeState(next);
    try {
      localStorage.setItem(MODE_STORAGE_KEY, next);
    } catch {
      // Same as useAlarms: the toggle works, it just won't be remembered.
    }
  }, []);

  const resetLayout = useCallback(() => setLayout(DEFAULT_LAYOUT), [setLayout]);

  return { mode, setMode, layout, setLayout, resetLayout };
}
