import { useContext, useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { createPortal } from "react-dom";

import { DockviewDefaultTab } from "dockview-react";
import type { IDockviewPanelHeaderProps } from "dockview-react";

import { chartCopyTitle } from "./ChartCopyPanel";
import { DockContext, WIDGET_TITLES, nextCopyId, type PanelParams } from "./dockShared";

const MENU_WIDTH = 200;
const MENU_HEIGHT = 130;

/** The dock's tab with a right-click menu. dockview's own tab context menu
 * (`getTabContextMenuItems`) is part of its paid ContextMenu module and a
 * no-op without it -- and the browser's menu still shows unless the event
 * is cancelled -- so this is the dashboard's own: the default tab with an
 * onContextMenu, and a small portalled menu positioned at the pointer, the
 * same shape as ChartWidget's Levels dropdown. */
export function DockTab(props: IDockviewPanelHeaderProps<PanelParams>) {
  const dock = useContext(DockContext);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    // Deferred by a tick so the contextmenu's own mousedown does not close
    // the menu it just opened.
    const id = window.setTimeout(() => {
      document.addEventListener("mousedown", close);
      document.addEventListener("keydown", onKey);
      window.addEventListener("resize", close);
      document.addEventListener("scroll", close, true);
    }, 0);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      document.removeEventListener("scroll", close, true);
    };
  }, [menu]);

  const open = (e: MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({
      x: Math.min(e.clientX, window.innerWidth - MENU_WIDTH - 8),
      y: Math.min(e.clientY, window.innerHeight - MENU_HEIGHT - 8),
    });
  };

  const { api, containerApi } = props;
  const widgetId = props.params.widgetId;
  const floating = api.location.type === "floating";

  const openCopy = () => {
    setMenu(null);
    const symbol = widgetId === "chart" ? (dock?.selectedSymbol ?? undefined) : undefined;
    const params: PanelParams = { widgetId, copy: true, ...(symbol ? { symbol } : {}) };
    containerApi.addPanel({
      id: nextCopyId(containerApi, widgetId),
      component: "widget",
      params,
      title: widgetId === "chart" ? chartCopyTitle(symbol ?? null) : `${WIDGET_TITLES[widgetId]} (copy)`,
      position: { referencePanel: api.id, direction: "right" },
    });
  };

  const float = () => {
    setMenu(null);
    const panel = containerApi.getPanel(api.id);
    if (!panel) return;
    containerApi.addFloatingGroup(panel, { width: 640, height: 420, x: 80, y: 80 });
  };

  const close = () => {
    setMenu(null);
    api.close();
  };

  return (
    <>
      <DockviewDefaultTab {...props} onContextMenu={open} />
      {menu &&
        createPortal(
          <div
            className="dock-tab-menu"
            role="menu"
            style={{ top: menu.y, left: menu.x }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <button type="button" role="menuitem" onClick={openCopy} title={
              widgetId === "chart"
                ? "A second chart pinned to its own symbol, as a new tab beside this one"
                : "A second instance of this widget as a new tab beside this one"
            }>
              Open in new window
            </button>
            <button type="button" role="menuitem" onClick={float} disabled={floating} title="Detach this tab into a floating window inside the dashboard">
              Float
            </button>
            <hr />
            <button type="button" role="menuitem" onClick={close}>
              Close
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
