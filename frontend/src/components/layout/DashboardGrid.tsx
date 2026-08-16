import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { GridLayout } from "react-grid-layout";
import type { Layout } from "react-grid-layout";

import {
  GRID_COLS,
  GRID_MARGIN,
  GRID_ROWS,
  WIDGET_IDS,
  type WidgetId,
} from "../../hooks/useDashboardLayout";

interface DashboardGridProps {
  layout: Layout;
  onLayoutChange: (layout: Layout) => void;
  /** Widget elements by stable id. Build this memoized in the caller so a
   * drag can't remount a widget (see WidgetId's docs for what that breaks). */
  widgets: Record<WidgetId, ReactNode>;
}

/** Floor so a very short viewport still yields a usable (if scrolling) grid
 * instead of collapsing every cell to nothing. */
const MIN_ROW_HEIGHT = 12;

/**
 * Measures both dimensions of an element with one ResizeObserver, mirroring
 * the pattern ScannerHeatmap already uses. react-grid-layout ships
 * useContainerWidth, but that only reports width and we need height too --
 * one observer on the element beats stacking a second one on top of it.
 */
function useContainerSize() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, ...size };
}

/**
 * Freely repositionable widget grid -- the draggable-grid counterpart to
 * ResizablePanels, which only resizes fixed slots. Drag a widget by its
 * header to move it; drag the bottom-right corner to resize; neighbours
 * compact out of the way. The arrangement is persisted by
 * useDashboardLayout.
 *
 * rowHeight is derived from the measured container height rather than fixed,
 * because react-grid-layout's autoSize makes the container's height
 * content-driven (max(y+h) x rowHeight) whereas .app-shell is a fixed 100vh.
 * Dividing the real height by GRID_ROWS makes a layout that spans all
 * GRID_ROWS fill the viewport exactly; a taller arrangement scrolls.
 */
export function DashboardGrid({ layout, onLayoutChange, widgets }: DashboardGridProps) {
  const { ref, width, height } = useContainerSize();

  const rowHeight =
    height > 0
      ? Math.max(MIN_ROW_HEIGHT, (height - GRID_MARGIN * (GRID_ROWS - 1)) / GRID_ROWS)
      : 0;
  // First paint happens before the observer has reported, and passing a
  // zero width would have the grid compute every cell at zero.
  const measured = width > 0 && rowHeight > 0;

  return (
    <div ref={ref} className="dashboard-grid">
      {measured && (
        <GridLayout
          width={width}
          layout={layout}
          onLayoutChange={onLayoutChange}
          gridConfig={{
            cols: GRID_COLS,
            rowHeight,
            margin: [GRID_MARGIN, GRID_MARGIN],
            // .dashboard-main already pads the layout host; without this the
            // grid would add its own padding on top and overflow.
            containerPadding: [0, 0],
          }}
          dragConfig={{
            enabled: true,
            // Only the widget header grabs, so tables stay selectable, the
            // chart still pans, and heatmap tiles stay clickable.
            handle: ".widget-header",
            // Headers carry real controls (scanner tabs, timeframe selects).
            cancel: "button, a, input, select, label",
          }}
          resizeConfig={{ enabled: true, handles: ["se"] }}
        >
          {WIDGET_IDS.map((id) => (
            <div key={id} className="grid-cell">
              {widgets[id]}
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
