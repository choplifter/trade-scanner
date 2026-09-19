/** Interface preferences that follow the person rather than the browser.
 *
 * The settings object, the layouts and the small per-widget toggles were
 * localStorage-only from before real logins existed (the watchlist's story,
 * see hooks/useWatchlist.ts). That made them per-browser: two people
 * sharing one shared a colour scheme and a dock layout, and the same person
 * on a second machine started from defaults.
 *
 * localStorage stays -- as a cache, so the first paint is not blocked on a
 * round trip and the app keeps working offline -- but it is now keyed per
 * user (`app:settings@u4`), and the server's copy wins once it arrives.
 *
 * Values are stored as the exact string localStorage would hold, so each
 * module keeps parsing its own shape (see api/settings.ts, the layout
 * hooks) and the backend keeps them opaque (app/prefs/store.py).
 */

import { API_BASE, checkUnauthorized } from "./http";

/** What follows the person. Deliberately not everything in localStorage:
 * `trading:account-mode` stays per browser, because which account an order
 * goes to should not change on this machine because another machine
 * switched, and the seeding flags (`chart:*Seeded`) are per-browser
 * bookkeeping rather than preferences. */
export const SYNCED_PREF_KEYS = [
  "app:settings",
  "layout:dock",
  "layout:grid",
  "layout:mode",
  "chart:cursorMode",
  "chart:visibleIndicators",
  "chart:visibleTradeLevels",
  "newsFeed:rankedOnly",
  "options:riskChartOpen",
  "alarms:enabled",
] as const;

const PUSH_DEBOUNCE_MS = 500;

type RemoteListener = (raw: string) => void;

let userId: number | null = null;
let hydrated = false;
const listeners = new Map<string, Set<RemoteListener>>();
const timers = new Map<string, number>();

/** The per-user cache key. Before the user is known (the login screen, the
 * moment before /auth/me answers) the plain key is read, which is also
 * where everything written before this existed still sits. */
function scoped(key: string): string {
  return userId == null ? key : `${key}@u${userId}`;
}

export function getStored(key: string): string | null {
  try {
    return localStorage.getItem(scoped(key)) ?? (userId == null ? null : localStorage.getItem(key));
  } catch {
    return null;
  }
}

export function setStored(key: string, raw: string): void {
  try {
    localStorage.setItem(scoped(key), raw);
  } catch {
    // Storage disabled (private browsing): the session still works, and
    // the server copy below is what carries the change anyway.
  }
  pushLater(key, raw);
}

export function removeStored(key: string): void {
  try {
    localStorage.removeItem(scoped(key));
  } catch {
    // As above.
  }
  if (userId == null) return;
  void fetch(`${API_BASE}/prefs/${encodeURIComponent(key)}`, { method: "DELETE", credentials: "include" }).catch(
    () => undefined,
  );
}

/** Called when the server's value for `key` arrives (on hydration, i.e.
 * after the first paint). Modules use it to adopt the stored value they
 * could not wait for. */
export function subscribeRemote(key: string, listener: RemoteListener): () => void {
  const set = listeners.get(key) ?? new Set<RemoteListener>();
  set.add(listener);
  listeners.set(key, set);
  return () => set.delete(listener);
}

function pushLater(key: string, raw: string): void {
  if (userId == null) return;
  const existing = timers.get(key);
  if (existing !== undefined) window.clearTimeout(existing);
  timers.set(
    key,
    window.setTimeout(() => {
      timers.delete(key);
      void push(key, raw);
    }, PUSH_DEBOUNCE_MS),
  );
}

async function push(key: string, raw: string): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/prefs/${encodeURIComponent(key)}`, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(raw),
    });
    checkUnauthorized(res);
  } catch {
    // A preference is not worth an error banner: the local copy holds, and
    // the next change pushes again.
  }
}

/** Adopt this user's stored preferences, and hand up whatever this browser
 * holds that the server does not yet -- which is how everything saved
 * before this feature existed moves across, once, on the next login. */
export async function hydratePrefs(id: number): Promise<void> {
  const switching = userId !== id;
  userId = id;
  hydrated = false;
  let stored: Record<string, unknown> = {};
  try {
    const res = await fetch(`${API_BASE}/prefs`, { credentials: "include" });
    checkUnauthorized(res);
    if (res.ok) stored = ((await res.json()) as { prefs?: Record<string, unknown> }).prefs ?? {};
  } catch {
    // Offline or the endpoint is not there: the cached values stand.
    return;
  }
  for (const key of SYNCED_PREF_KEYS) {
    const remote = stored[key];
    if (typeof remote === "string") {
      try {
        localStorage.setItem(scoped(key), remote);
      } catch {
        // Cache only; the listeners below still get the value.
      }
      listeners.get(key)?.forEach((fn) => fn(remote));
      continue;
    }
    // Nothing stored for this user yet. Whatever this browser holds
    // becomes their starting point -- unless we have just switched user
    // and it belonged to the previous one (the unscoped legacy key is
    // taken, since that is what a single-user browser wrote).
    const local = switching ? readOwn(key) : getStored(key);
    if (local != null) void push(key, local);
  }
  hydrated = true;
}

/** This user's own cached value, ignoring the shared legacy key. */
function readOwn(key: string): string | null {
  try {
    return localStorage.getItem(scoped(key));
  } catch {
    return null;
  }
}

/** Forgets the user, so the login screen and the next person do not read
 * the last one's cache. Called on logout. */
export function resetPrefs(): void {
  timers.forEach((id) => window.clearTimeout(id));
  timers.clear();
  userId = null;
  hydrated = false;
}

export function prefsHydrated(): boolean {
  return hydrated;
}
