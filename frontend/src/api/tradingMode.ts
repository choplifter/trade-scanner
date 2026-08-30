/** Which trading backend the app talks to: the real (paper) Alpaca account,
 * or Simulation Mode's fully local order book (backend/app/trading/sim).
 *
 * A module-level singleton with its own tiny pub/sub -- same shared-instance
 * spirit as api/ws.ts's sockets -- rather than React state, because the
 * thing that actually needs to know the mode is tradingPath() below, which
 * every trading-specific api/http.ts function calls before every request.
 * useTradingMode (hooks/useTradingMode.ts) is a thin React view over this
 * for components that need to render the current mode.
 */

export type TradingMode = "live" | "simulation";

const STORAGE_KEY = "trading:mode";

type Listener = (mode: TradingMode) => void;
const listeners = new Set<Listener>();

function load(): TradingMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === "simulation" ? "simulation" : "live";
  } catch {
    return "live";
  }
}

let mode: TradingMode = load();

export function getTradingMode(): TradingMode {
  return mode;
}

export function setTradingMode(next: TradingMode): void {
  if (next === mode) return;
  mode = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Private browsing / storage disabled -- the switch still works for
    // this session, it just won't be remembered next time.
  }
  listeners.forEach((fn) => fn(mode));
}

export function subscribeTradingMode(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Rewrites a /trading/... path to /trading/sim/... in simulation mode --
 * the one thing every trading-specific api/http.ts function wraps its path
 * with, so the generic getJson/postJson/deleteJson/patchJson helpers (and
 * every non-trading caller) stay untouched. */
export function tradingPath(path: string): string {
  return mode === "simulation" ? path.replace(/^\/trading\//, "/trading/sim/") : path;
}
