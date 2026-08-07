import { Fragment, useCallback, useEffect, useRef, useState } from "react";

interface ResizablePanelsProps {
  direction: "row" | "column";
  storageKey: string;
  /** Fractions per child, should sum to ~1. Read once on mount as the fallback. */
  defaultSizes: number[];
  minSizePx?: number;
  children: React.ReactNode[];
}

function loadSizes(key: string, fallback: number[]): number[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.length === fallback.length) return parsed as number[];
  } catch {
    // Corrupt/foreign localStorage value -- fall back to defaults rather than crash the layout.
  }
  return fallback;
}

interface DragState {
  index: number;
  startPos: number;
  startSizes: number[];
  containerSize: number;
}

/**
 * Drag-resizable flex panels (vertical stack or horizontal row), sized as
 * fractions of the container and persisted to localStorage. Panels never
 * auto-grow with their content -- each is a fixed fraction of the
 * container's own size, so one panel's content (e.g. a table that gained
 * rows) can no longer steal space from a sibling; it just scrolls inside
 * its own bounds instead.
 */
export function ResizablePanels({
  direction,
  storageKey,
  defaultSizes,
  minSizePx = 120,
  children,
}: ResizablePanelsProps) {
  const [sizes, setSizes] = useState(() => loadSizes(storageKey, defaultSizes));
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);

  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(sizes));
  }, [sizes, storageKey]);

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const pos = direction === "row" ? e.clientX : e.clientY;
      const delta = (pos - drag.startPos) / drag.containerSize;
      const minFrac = drag.containerSize > 0 ? minSizePx / drag.containerSize : 0;

      let a = drag.startSizes[drag.index] + delta;
      let b = drag.startSizes[drag.index + 1] - delta;
      if (a < minFrac) {
        b -= minFrac - a;
        a = minFrac;
      }
      if (b < minFrac) {
        a -= minFrac - b;
        b = minFrac;
      }

      setSizes((prev) => {
        const next = [...prev];
        next[drag.index] = a;
        next[drag.index + 1] = b;
        return next;
      });
    },
    [direction, minSizePx],
  );

  const endDrag = useCallback(() => {
    dragRef.current = null;
    setDraggingIndex(null);
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endDrag);
  }, [onPointerMove]);

  useEffect(() => () => endDrag(), [endDrag]);

  const startDrag = (index: number) => (e: React.PointerEvent) => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    dragRef.current = {
      index,
      startPos: direction === "row" ? e.clientX : e.clientY,
      startSizes: sizes,
      containerSize: direction === "row" ? rect.width : rect.height,
    };
    setDraggingIndex(index);
    document.body.style.userSelect = "none";
    document.body.style.cursor = direction === "row" ? "col-resize" : "row-resize";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
  };

  return (
    <div ref={containerRef} className={`resizable-panels resizable-${direction}`}>
      {children.map((child, i) => (
        <Fragment key={i}>
          <div
            className="resizable-panel"
            style={direction === "row" ? { width: `${sizes[i] * 100}%` } : { height: `${sizes[i] * 100}%` }}
          >
            {child}
          </div>
          {i < children.length - 1 && (
            <div
              className={`resize-handle resize-handle-${direction}${draggingIndex === i ? " dragging" : ""}`}
              onPointerDown={startDrag(i)}
            />
          )}
        </Fragment>
      ))}
    </div>
  );
}
