import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { getStored, setStored } from "../../api/prefs";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Extra class on the panel, for a dialog that needs its own size
   * (the Settings dialog is wider than an order confirmation). */
  className?: string;
  /** No backdrop: the page behind stays usable and a click outside does
   * not close it. For reference windows -- the help pages, Settings --
   * read beside what they describe. A confirmation stays modal. */
  modeless?: boolean;
  /** Remembers where the window was dragged to under this name, so it
   * opens there next time. Without it a dragged window re-centres. */
  positionKey?: string;
}

interface Point {
  x: number;
  y: number;
}

// How much of a window must stay on screen: enough of the header to grab
// it again, whatever the window was dragged or the viewport resized to.
const KEEP_VISIBLE_X = 80;
const KEEP_VISIBLE_Y = 40;

function clampToViewport(pos: Point, width: number): Point {
  return {
    x: Math.min(Math.max(pos.x, KEEP_VISIBLE_X - width), window.innerWidth - KEEP_VISIBLE_X),
    y: Math.min(Math.max(pos.y, 0), window.innerHeight - KEEP_VISIBLE_Y),
  };
}

function loadPosition(key: string | undefined): Point | null {
  if (!key) return null;
  try {
    const raw = getStored(`modal:pos:${key}`);
    const parsed = raw ? (JSON.parse(raw) as Partial<Point>) : null;
    if (parsed && Number.isFinite(parsed.x) && Number.isFinite(parsed.y)) return { x: parsed.x!, y: parsed.y! };
  } catch {
    // A malformed value just means "centred".
  }
  return null;
}

/** A panel with a header, dragged by that header; over a backdrop by
 * default, floating with `modeless`. Shares its look with AlarmsOverlay,
 * which predates it and should migrate here eventually.
 *
 * Escape closes it. Dismissing an order confirmation should not require
 * aiming at a backdrop.
 *
 * Rendered through a portal to document.body, which is not optional here.
 * react-grid-layout positions every widget with a CSS transform, and a
 * transformed ancestor becomes the containing block for position:fixed --
 * so without the portal the backdrop is confined to the widget's own cell
 * (measured 629x800 instead of the 2560x1271 viewport) and then clipped by
 * .widget's overflow:hidden. The dialog opened, rendered correctly, and was
 * invisible: clicking Buy looked like nothing happened.
 *
 * Centred until dragged; a dragged window is placed by its top-left
 * corner in viewport pixels, clamped so its header can always be grabbed
 * again (also after the browser window shrinks).
 *
 * No focus trap, matching the rest of this single-user app. Nothing
 * autofocuses either: for an order ticket, accidental confirmation is the
 * dangerous direction and accidental dismissal is free.
 */
export function Modal({ open, title, onClose, children, className, modeless = false, positionKey }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Point | null>(() => loadPosition(positionKey));
  const [, setViewportTick] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Re-render on resize so the clamp below follows the viewport.
    const onResize = () => setViewportTick((n) => n + 1);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [open, onClose]);

  if (!open) return null;

  const startDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    const panel = panelRef.current;
    if (!panel) return;
    e.preventDefault();
    const rect = panel.getBoundingClientRect();
    const grab = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    let last: Point = { x: rect.left, y: rect.top };
    const onMove = (ev: PointerEvent) => {
      last = clampToViewport({ x: ev.clientX - grab.x, y: ev.clientY - grab.y }, rect.width);
      setPos(last);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("modal-dragging");
      if (positionKey) setStored(`modal:pos:${positionKey}`, JSON.stringify(last));
    };
    document.body.classList.add("modal-dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = panelRef.current?.offsetWidth ?? 320;
  const placed = pos ? clampToViewport(pos, width) : null;
  const style: CSSProperties | undefined = placed ? { position: "fixed", left: placed.x, top: placed.y, transform: "none" } : undefined;

  const panel = (
    <div
      ref={panelRef}
      className={`modal-panel${modeless ? " modal-floating" : ""}${className ? ` ${className}` : ""}`}
      role="dialog"
      aria-modal={modeless ? undefined : "true"}
      aria-label={title}
      style={style}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="modal-panel-header modal-drag-handle" onPointerDown={startDrag} title="Drag to move">
        <h2>{title}</h2>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      {children}
    </div>
  );

  return createPortal(
    modeless ? (
      panel
    ) : (
      <div className="modal-backdrop" onClick={onClose}>
        {panel}
      </div>
    ),
    document.body,
  );
}
