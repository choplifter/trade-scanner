import { useEffect, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
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
 * No portal and no focus trap, matching the rest of this single-user app --
 * a deliberate omission rather than an oversight. Note that nothing here
 * autofocuses: for an order ticket, accidental confirmation is the dangerous
 * direction and accidental dismissal is free.
 */
export function Modal({ open, title, onClose, children }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-panel"
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
    </div>
  );
}
