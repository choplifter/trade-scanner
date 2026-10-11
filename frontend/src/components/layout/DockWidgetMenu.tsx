import { useEffect, useRef, useState } from "react";

import { reopenWidget, useClosedDockIds } from "./dockLayouts";
import { WIDGET_TITLES } from "./dockShared";

/** The header's "+ Widget": the dock widgets with no open panel -- Dockview
 * has no affordance of its own for this (closing a panel just removes it).
 * Click one to open it again. */
export function DockWidgetMenu() {
  const closedIds = useClosedDockIds();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (closedIds.length === 0) return null;

  return (
    <span className="dock-profile" ref={rootRef}>
      <button
        type="button"
        className="layout-reset-button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="Open a widget that has no panel in the dock"
      >
        + Widget ({closedIds.length})
      </button>
      {open && (
        <div className="dock-profile-menu" role="menu">
          {closedIds.map((id) => (
            <div key={id} className="dock-profile-row">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  if (reopenWidget(id)) setOpen(false);
                }}
              >
                {WIDGET_TITLES[id]}
              </button>
            </div>
          ))}
        </div>
      )}
    </span>
  );
}
