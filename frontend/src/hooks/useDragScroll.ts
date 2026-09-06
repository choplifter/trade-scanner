import { useCallback, useEffect, useRef, type PointerEvent as ReactPointerEvent, type MouseEvent as ReactMouseEvent } from "react";

/** A press that moves less than this is a click on whatever is under the
 * pointer, not a drag of the strip. */
const DRAG_THRESHOLD_PX = 4;

export interface DragScrollHandlers<T extends HTMLElement> {
  ref: (node: T | null) => void;
  onPointerDown: (e: ReactPointerEvent<T>) => void;
  onPointerMove: (e: ReactPointerEvent<T>) => void;
  onPointerUp: (e: ReactPointerEvent<T>) => void;
  onPointerCancel: (e: ReactPointerEvent<T>) => void;
  onClickCapture: (e: ReactMouseEvent<T>) => void;
}

/**
 * Drag a horizontally overflowing strip with the mouse (or a finger), the
 * way a chart's time axis is dragged: press anywhere on it, move, release.
 * A plain wheel over the strip scrolls it sideways too, so the expiry strip
 * does not need Shift held. A press that barely moves is still a click on
 * the button under it; one that dragged swallows the click that follows.
 */
export function useDragScroll<T extends HTMLElement>(): DragScrollHandlers<T> {
  const nodeRef = useRef<T | null>(null);
  const drag = useRef<{ pointerId: number; startX: number; startLeft: number; moved: boolean } | null>(null);
  const swallowClick = useRef(false);

  useEffect(() => {
    const node = nodeRef.current;
    if (!node) return;
    const onWheel = (e: WheelEvent) => {
      // Only a vertical wheel is redirected; a trackpad's horizontal swipe
      // already scrolls the strip natively.
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      if (node.scrollWidth <= node.clientWidth) return;
      e.preventDefault();
      node.scrollLeft += e.deltaY;
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  });

  const ref = useCallback((node: T | null) => {
    nodeRef.current = node;
  }, []);

  const onPointerDown = useCallback((e: ReactPointerEvent<T>) => {
    if (e.button !== 0) return;
    const node = nodeRef.current;
    if (!node || node.scrollWidth <= node.clientWidth) return;
    drag.current = { pointerId: e.pointerId, startX: e.clientX, startLeft: node.scrollLeft, moved: false };
    swallowClick.current = false;
  }, []);

  const onPointerMove = useCallback((e: ReactPointerEvent<T>) => {
    const d = drag.current;
    const node = nodeRef.current;
    if (!d || !node || e.pointerId !== d.pointerId) return;
    const dx = e.clientX - d.startX;
    if (!d.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
    if (!d.moved) {
      d.moved = true;
      node.setPointerCapture(e.pointerId);
      node.classList.add("dragging");
    }
    node.scrollLeft = d.startLeft - dx;
  }, []);

  const end = useCallback((e: ReactPointerEvent<T>) => {
    const d = drag.current;
    const node = nodeRef.current;
    if (!d || e.pointerId !== d.pointerId) return;
    drag.current = null;
    if (node) {
      node.classList.remove("dragging");
      if (node.hasPointerCapture(e.pointerId)) node.releasePointerCapture(e.pointerId);
    }
    swallowClick.current = d.moved;
  }, []);

  const onClickCapture = useCallback((e: ReactMouseEvent<T>) => {
    if (!swallowClick.current) return;
    swallowClick.current = false;
    e.preventDefault();
    e.stopPropagation();
  }, []);

  return { ref, onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end, onClickCapture };
}
