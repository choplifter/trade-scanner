import { useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { DockviewReact, themeDark, themeLight } from "dockview-react";
import type {
  DockviewReadyEvent,
  IDockviewHeaderActionsProps,
  IDockviewPanelProps,
  SerializedDockview,
} from "dockview-react";
import "dockview-react/dist/styles/dockview.css";

import { WIDGET_IDS, type WidgetId } from "../../hooks/useDashboardLayout";
import { ChartCopyPanel } from "./ChartCopyPanel";
import { DockTab } from "./DockTab";
import { DockContext, WIDGET_TITLES, type PanelParams } from "./dockShared";

type DockviewApi = DockviewReadyEvent["api"];

interface DockviewDashboardProps {
  /** Widget elements by stable id. Same object DashboardGrid/ResizablePanels
   * render from -- build it memoized in the caller so a re-render can't
   * remount a widget (see WidgetId's docs for what that breaks). */
  widgets: Record<WidgetId, ReactNode>;
  /** The dashboard's current underlying symbol -- what a chart copy opened
   * from a tab's context menu starts pinned to. */
  selectedSymbol: string | null;
}

/** See DockContext (dockShared.ts) for why panels read the widget record
 * from context. A copy (context menu -> "Open in new window") renders a
 * second instance: for the chart a ChartCopyPanel pinned to its own
 * symbol, for anything else the same element again. */
function WidgetPanel(props: IDockviewPanelProps<PanelParams>) {
  const dock = useContext(DockContext);
  const { widgetId, copy, symbol } = props.params;
  if (copy && widgetId === "chart") {
    return <ChartCopyPanel api={props.api} initialSymbol={symbol ?? null} />;
  }
  return <>{dock?.widgets[widgetId] ?? null}</>;
}

// Stable reference (module scope, not recreated per render) -- components
// registered here are looked up by id when Dockview reads a panel's
// `component` field, not re-supplied per panel.
const COMPONENTS = { widget: WidgetPanel };

/** Rendered once per group's tab strip (top-right) -- dockview-react has no
 * default maximize button of its own, this is the
 * `rightHeaderActionsComponent` slot IDockviewReactProps exposes for
 * exactly this. `containerApi.maximizeGroup`/`exitMaximizedGroup` (rather
 * than this group's own `api.maximize()`) so this stays the single source
 * of truth other groups' maximize state is also affected by -- only one
 * group can be maximized at a time. */
function GroupHeaderActions({ group, containerApi, activePanel }: IDockviewHeaderActionsProps) {
  const [maximized, setMaximized] = useState(() => group.api.isMaximized());

  useEffect(() => {
    const disposable = containerApi.onDidMaximizedGroupChange(() => setMaximized(group.api.isMaximized()));
    return () => disposable.dispose();
  }, [group, containerApi]);

  if (!activePanel) return null; // between panels, e.g. a group closing its last tab

  return (
    <button
      type="button"
      className="dockview-header-action"
      onClick={() => (maximized ? containerApi.exitMaximizedGroup() : containerApi.maximizeGroup(activePanel))}
      title={maximized ? "Restore" : "Maximize"}
    >
      {maximized ? "🗗" : "🗖"}
    </button>
  );
}

function addWidgetPanel(
  api: DockviewApi,
  id: WidgetId,
  position?: { referencePanel: WidgetId; direction: "left" | "right" | "above" | "below" | "within" },
) {
  api.addPanel({
    id,
    component: "widget",
    params: { widgetId: id },
    title: WIDGET_TITLES[id],
    ...(position ? { position } : {}),
  } satisfies Parameters<DockviewApi["addPanel"]>[0]);
}

/** Ids of the singleton widgets with an open panel -- copies never count. */
function openWidgetIds(api: DockviewApi): Set<WidgetId> {
  return new Set(
    api.panels.filter((p) => !(p.params as PanelParams | undefined)?.copy).map((p) => p.id as WidgetId),
  );
}

const DOCK_LAYOUT_KEY = "layout:dock";
/** Bump when buildDefaultLayout's widget set changes -- same
 * discard-rather-than-migrate convention as useDashboardLayout's
 * LAYOUT_VERSION, for the same reason: a saved layout missing a newly
 * added widget (or referencing a removed one) is worse than starting over. */
// 2: added "trade_journal".
// 3: added "options", tabbed with "trading".
const DOCK_LAYOUT_VERSION = 3;
const DOCK_WRITE_DEBOUNCE_MS = 200;

interface StoredDockLayout {
  version: number;
  layout: SerializedDockview;
}

function loadDockLayout(): SerializedDockview | null {
  try {
    const raw = localStorage.getItem(DOCK_LAYOUT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredDockLayout>;
    return parsed.version === DOCK_LAYOUT_VERSION && parsed.layout ? parsed.layout : null;
  } catch {
    // Corrupt/foreign/stale localStorage value -- fall back to the default
    // layout rather than crash the dashboard, same as useDashboardLayout's
    // loadLayout.
    return null;
  }
}

function saveDockLayout(layout: SerializedDockview) {
  try {
    localStorage.setItem(DOCK_LAYOUT_KEY, JSON.stringify({ version: DOCK_LAYOUT_VERSION, layout } satisfies StoredDockLayout));
  } catch {
    // Storage disabled -- the session still works, it just won't be
    // remembered next time.
  }
}

/** Builds a default arrangement roughly mirroring panels mode's layout
 * (scanner+chart+trading/watchlist across the top, analytics widgets
 * tabbed together below) -- the starting point for a first-ever mount, or
 * whenever loadDockLayout() has nothing usable saved. */
function buildDefaultLayout(api: DockviewApi) {
  addWidgetPanel(api, "scanner");
  addWidgetPanel(api, "chart", { referencePanel: "scanner", direction: "right" });
  addWidgetPanel(api, "trading", { referencePanel: "chart", direction: "right" });
  addWidgetPanel(api, "watchlist", { referencePanel: "trading", direction: "below" });
  addWidgetPanel(api, "symbol_info", { referencePanel: "scanner", direction: "below" });
  addWidgetPanel(api, "news_feed", { referencePanel: "symbol_info", direction: "within" });
  addWidgetPanel(api, "ideas", { referencePanel: "symbol_info", direction: "below" });
  addWidgetPanel(api, "benchmark", { referencePanel: "ideas", direction: "within" });
  addWidgetPanel(api, "history", { referencePanel: "ideas", direction: "within" });
  addWidgetPanel(api, "gex_plan", { referencePanel: "ideas", direction: "within" });
  addWidgetPanel(api, "replay", { referencePanel: "ideas", direction: "within" });
  addWidgetPanel(api, "trade_journal", { referencePanel: "ideas", direction: "within" });
  addWidgetPanel(api, "options", { referencePanel: "trading", direction: "within" });
}

/**
 * Prototype: the dashboard's widgets hosted in `dockview-react` (VS-Code
 * style docking -- tabs, drag-to-dock/rearrange, float, maximize, close/
 * reopen, popout to a real window) instead of DashboardGrid/ResizablePanels.
 * Evaluate this against those two before deciding whether to replace either.
 *
 * Dockview-only chrome: the maximize button and reopen-a-closed-widget
 * menu below, and the tab's own right-click menu (DockTab.tsx: open a
 * second instance as a new tab, float, close).
 */
export function DockviewDashboard({ widgets, selectedSymbol }: DockviewDashboardProps) {
  // Matches the app's own system-preference-only dark mode (see styles.css's
  // prefers-color-scheme block) -- read once, not reactive to a live OS
  // theme change, since dockview-react has no equivalent of :root
  // recomputing an @media query -- see the theme prop.
  const theme = useMemo(
    () => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? themeDark : themeLight),
    [],
  );

  // Only set once, in onReady -- exists so the reopen menu below can call
  // addWidgetPanel outside of Dockview's own event flow.
  const [containerApi, setContainerApi] = useState<DockviewApi | null>(null);
  // Which WIDGET_IDS currently have an open panel, for the reopen menu.
  // Dockview doesn't expose this as reactive state on its own -- resynced
  // off the same onDidLayoutChange the persistence write already listens to.
  const [openIds, setOpenIds] = useState<Set<WidgetId>>(new Set());

  const onReady = (event: DockviewReadyEvent) => {
    const saved = loadDockLayout();
    if (saved) {
      event.api.fromJSON(saved);
    } else {
      buildDefaultLayout(event.api);
    }
    setContainerApi(event.api);
    setOpenIds(openWidgetIds(event.api));

    // onDidLayoutChange fires on every add/move/resize -- the library's own
    // doc comment on the event flags it as "worth debouncing" -- same
    // WRITE_DEBOUNCE_MS reasoning as useDashboardLayout's grid persistence
    // (react-grid-layout fires just as often during a drag). openIds is
    // cheap to recompute so it isn't debounced -- only the localStorage
    // write is.
    let writeTimer: number | undefined;
    event.api.onDidLayoutChange(() => {
      setOpenIds(openWidgetIds(event.api));
      if (writeTimer !== undefined) window.clearTimeout(writeTimer);
      writeTimer = window.setTimeout(() => saveDockLayout(event.api.toJSON()), DOCK_WRITE_DEBOUNCE_MS);
    });
  };

  const closedIds = WIDGET_IDS.filter((id) => !openIds.has(id));
  const dockValue = useMemo(() => ({ widgets, selectedSymbol }), [widgets, selectedSymbol]);

  return (
    <DockContext.Provider value={dockValue}>
      <div className="dockview-dashboard">
        {containerApi && closedIds.length > 0 && (
          <ReopenWidgetMenu api={containerApi} closedIds={closedIds} />
        )}
        <DockviewReact
          className="dockview-root"
          theme={theme}
          onReady={onReady}
          components={COMPONENTS}
          defaultTabComponent={DockTab}
          rightHeaderActionsComponent={GroupHeaderActions}
        />
      </div>
    </DockContext.Provider>
  );
}

/** Corner menu listing widgets with no open panel -- Dockview has no
 * built-in affordance for this (closing a panel just removes it), so this
 * is the dashboard's own. Click a widget to reopen it as a new panel;
 * position is omitted (unlike buildDefaultLayout's calls) so Dockview falls
 * back to its own default placement -- there's no single "right" spot to
 * dock an arbitrary reopened widget back into an arrangement the user may
 * have since rearranged. */
function ReopenWidgetMenu({ api, closedIds }: { api: DockviewApi; closedIds: WidgetId[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="dockview-reopen">
      <button type="button" className="dockview-reopen-button" onClick={() => setOpen((v) => !v)}>
        + Widget ({closedIds.length})
      </button>
      {open && (
        <div className="dockview-reopen-menu" role="menu">
          {closedIds.map((id) => (
            <button
              key={id}
              type="button"
              role="menuitem"
              onClick={() => {
                addWidgetPanel(api, id);
                setOpen(false);
              }}
            >
              {WIDGET_TITLES[id]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

