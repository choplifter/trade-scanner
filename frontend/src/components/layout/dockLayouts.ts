import { useSyncExternalStore } from "react";

import type { DockviewApi, SerializedDockview } from "dockview-react";

import { getStored, setStored, subscribeRemote } from "../../api/prefs";
import { WIDGET_TITLES, type DockWidgetId, type PanelParams } from "./dockShared";

type Direction = "left" | "right" | "above" | "below" | "within";

export function addWidgetPanel(
  api: DockviewApi,
  id: DockWidgetId,
  position?: { referencePanel: DockWidgetId; direction: Direction },
  size?: { width?: number; height?: number },
) {
  api.addPanel({
    id,
    component: "widget",
    params: { widgetId: id } satisfies PanelParams,
    title: WIDGET_TITLES[id],
    ...(position ? { position } : {}),
    ...(size?.width ? { initialWidth: size.width } : {}),
    ...(size?.height ? { initialHeight: size.height } : {}),
  } satisfies Parameters<DockviewApi["addPanel"]>[0]);
}

/** Builds a default arrangement roughly mirroring panels mode's layout
 * (scanner+chart+trading/watchlist across the top, analytics widgets
 * tabbed together below) -- the starting point for a first-ever mount, or
 * whenever nothing usable is saved. */
export function buildDefaultLayout(api: DockviewApi) {
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
  addWidgetPanel(api, "earnings", { referencePanel: "ideas", direction: "within" });
}

/** Share of the dock's width or height, for a new group's initial size. */
function share(api: DockviewApi, axis: "width" | "height", fraction: number): number {
  return Math.round((axis === "width" ? api.width : api.height) * fraction);
}

/** Finding something to write: the screener large on the left, the name
 * it turns up on the chart and in the chain beside it. */
function buildScreening(api: DockviewApi) {
  addWidgetPanel(api, "options_screener");
  addWidgetPanel(api, "watchlist", { referencePanel: "options_screener", direction: "within" });
  addWidgetPanel(api, "earnings", { referencePanel: "options_screener", direction: "within" });
  api.getPanel("options_screener")?.api.setActive();
  addWidgetPanel(
    api,
    "chart",
    { referencePanel: "options_screener", direction: "right" },
    { width: share(api, "width", 0.45) },
  );
  addWidgetPanel(api, "symbol_info", { referencePanel: "chart", direction: "within" });
  api.getPanel("chart")?.api.setActive();
  addWidgetPanel(api, "options", { referencePanel: "chart", direction: "below" }, { height: share(api, "height", 0.55) });
}

/** Building and watching a position: the chain and ticket on the right,
 * its risk chart large under the price chart, the open spreads below the
 * ticket. */
function buildTrading(api: DockviewApi) {
  addWidgetPanel(api, "chart");
  addWidgetPanel(api, "options", { referencePanel: "chart", direction: "right" }, { width: share(api, "width", 0.55) });
  addWidgetPanel(api, "options_risk", { referencePanel: "chart", direction: "below" }, { height: share(api, "height", 0.45) });
  addWidgetPanel(api, "options_spreads", { referencePanel: "options", direction: "below" }, { height: share(api, "height", 0.35) });
  addWidgetPanel(api, "trading", { referencePanel: "options_spreads", direction: "within" });
  addWidgetPanel(api, "watchlist", { referencePanel: "options_spreads", direction: "within" });
  api.getPanel("options_spreads")?.api.setActive();
}

/** The day's expiry: intraday chart with the gamma levels beside it, the
 * chain and ticket, the risk chart and what is already open in view. */
function buildZeroDte(api: DockviewApi) {
  addWidgetPanel(api, "chart");
  addWidgetPanel(api, "options", { referencePanel: "chart", direction: "right" }, { width: share(api, "width", 0.5) });
  addWidgetPanel(api, "options_risk", { referencePanel: "chart", direction: "below" }, { height: share(api, "height", 0.4) });
  addWidgetPanel(api, "options_spreads", { referencePanel: "options_risk", direction: "right" });
  addWidgetPanel(api, "gex_plan", { referencePanel: "options", direction: "below" }, { height: share(api, "height", 0.3) });
  addWidgetPanel(api, "news_feed", { referencePanel: "gex_plan", direction: "within" });
  api.getPanel("gex_plan")?.api.setActive();
}

export interface BuiltInProfile {
  name: string;
  title: string;
  build: (api: DockviewApi) => void;
}

export const BUILT_IN_PROFILES: readonly BuiltInProfile[] = [
  { name: "Standard", title: "Every widget, the way the dock starts out", build: buildDefaultLayout },
  {
    name: "Screening",
    title: "Options screener large, the chart and the chain of the name it turns up beside it",
    build: buildScreening,
  },
  {
    name: "Trading",
    title: "Chain and ticket, the risk chart large under the price chart, open spreads in view",
    build: buildTrading,
  },
  {
    name: "0DTE",
    title: "Intraday chart, GEX plan, chain and ticket, risk chart and open spreads at once",
    build: buildZeroDte,
  },
];

// --- Named layouts --------------------------------------------------------

const PROFILES_KEY = "layout:dock:profiles";
const PROFILES_VERSION = 1;

export interface DockProfilesState {
  /** Arrangements saved under a name; one named like a built-in replaces it. */
  saved: Record<string, SerializedDockview>;
  /** The profile last switched to or saved, for the header's label. */
  active: string | null;
}

interface StoredProfiles extends DockProfilesState {
  version: number;
}

function parse(raw: string | null): DockProfilesState {
  try {
    if (!raw) return { saved: {}, active: null };
    const parsed = JSON.parse(raw) as Partial<StoredProfiles>;
    if (parsed.version !== PROFILES_VERSION || !parsed.saved || typeof parsed.saved !== "object") {
      return { saved: {}, active: null };
    }
    return { saved: parsed.saved, active: typeof parsed.active === "string" ? parsed.active : null };
  } catch {
    return { saved: {}, active: null };
  }
}

let state: DockProfilesState = parse(getStored(PROFILES_KEY));
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function commit(next: DockProfilesState) {
  state = next;
  setStored(PROFILES_KEY, JSON.stringify({ version: PROFILES_VERSION, ...next } satisfies StoredProfiles));
  emit();
}

// Another machine's profiles, once the server's copy arrives.
subscribeRemote(PROFILES_KEY, (raw) => {
  state = parse(raw);
  emit();
});

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useDockProfiles(): DockProfilesState {
  return useSyncExternalStore(subscribe, () => state);
}

/** The dock's api, set by DockviewDashboard while it is mounted -- the
 * header's profile menu lives outside the dock. */
let dockApi: DockviewApi | null = null;
const apiListeners = new Set<() => void>();

export function registerDockApi(api: DockviewApi | null) {
  dockApi = api;
  for (const l of apiListeners) l();
}

export function useDockReady(): boolean {
  return useSyncExternalStore(
    (l) => {
      apiListeners.add(l);
      return () => {
        apiListeners.delete(l);
      };
    },
    () => dockApi !== null,
  );
}

/** Where an arrangement made with no profile active is kept on a switch. */
export const UNNAMED_PROFILE = "My layout";

/** Replaces what the dock shows with the profile's arrangement: the saved
 * one under that name, else the built-in. What was on screen is kept
 * first under the profile it came from (or UNNAMED_PROFILE), like a
 * workspace -- a switch never loses an arrangement. The dock's own
 * autosave then keeps the new one as the working layout. */
export function applyProfile(name: string): boolean {
  const api = dockApi;
  if (!api) return false;
  const builtIn = BUILT_IN_PROFILES.find((p) => p.name === name);
  if (!state.saved[name] && !builtIn) return false;
  if (api.panels.length > 0 && state.active !== name) {
    state = { ...state, saved: { ...state.saved, [state.active ?? UNNAMED_PROFILE]: api.toJSON() } };
  }
  const saved = state.saved[name];
  api.clear();
  let done = false;
  if (saved) {
    try {
      api.fromJSON(saved);
      done = api.panels.length > 0;
    } catch (err) {
      console.error(`Dock profile "${name}" could not be restored`, err);
    }
  }
  if (!done) {
    api.clear();
    (builtIn?.build ?? buildDefaultLayout)(api);
  }
  commit({ ...state, active: name });
  return true;
}

/** Stores what the dock shows now under `name` (replacing one of that name). */
export function saveProfile(name: string): boolean {
  const api = dockApi;
  const trimmed = name.trim();
  if (!api || !trimmed || api.panels.length === 0) return false;
  commit({ saved: { ...state.saved, [trimmed]: api.toJSON() }, active: trimmed });
  return true;
}

/** Forgets a saved profile; a built-in of that name comes back as built. */
export function deleteProfile(name: string) {
  if (!(name in state.saved)) return;
  const saved = { ...state.saved };
  delete saved[name];
  commit({ saved, active: state.active === name ? null : state.active });
}

/** The dock was reset or rearranged into no profile in particular. */
export function clearActiveProfile() {
  if (state.active !== null) commit({ ...state, active: null });
}
