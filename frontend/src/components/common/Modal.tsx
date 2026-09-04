import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Extra class on the panel, for a dialog that needs its own size
   * (the Settings dialog is wider than an order confirmation). */
  className?: string;
}

/** A backdrop and a panel. Shares its look with AlarmsOverlay, which predates
 * it and should migrate here eventually -- not done in the same change that
 * introduced order confirmation, since that markup is bespoke list content
 * and the refactor is unrelated risk.
 *
 * Adds the one thing AlarmsOverlay lacks and a confirmation genuinely needs:
 * Escape closes it. Dismissing an order confirmation should not require
 * aiming at a backdrop.
 *
 * Rendered through a portal to document.body, which is not optional here.
 * react-grid-layout positions every widget with a CSS transform, and a
 * transformed ancestor becomes the containing block for position:fixed --
 * so without the portal the backdrop is confined to the widget's own cell
 * (measured 629x800 instead of the 2560x1271 viewport) and then clipped by
 * .widget's overflow:hidden. The dialog opened, rendered correctly, and was
 * invisible: clicking Buy looked like nothing happened. AlarmsOverlay never
 * hit this because it renders from the App root rather than inside a grid
 * item.
 *
 * No focus trap, matching the rest of this single-user app. Nothing
 * autofocuses either: for an order ticket, accidental confirmation is the
 * dangerous direction and accidental dismissal is free.
 */
export function Modal({ open, title, onClose, children, className }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className={`modal-panel${className ? ` ${className}` : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-panel-header">
          <h2>{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
