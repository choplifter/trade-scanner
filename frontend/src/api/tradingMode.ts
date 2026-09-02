/**
 * Which account the trading panel is talking to. A module-level singleton
 * (not React state) because api/http.ts has to consult it from plain
 * functions -- every trading call routes through tradingPath() below.
 *
 * "simulation": the local, broker-free practice book (/api/trading/sim).
 * "paper":      the Alpaca paper account (/api/trading) -- the default.
 * "live":       the real-money account (/api/trading/live). Never persisted:
 *               a page load always comes back in paper, and Live has to be
 *               chosen again, deliberately, every session. That is the one
 *               state a stale tab must not silently wake up in.
 *
 * The storage key is new (the old "trading:mode" stored "live" to mean the
 * paper account, before there was a real one) so nothing written by an
 * earlier build can be read as a request for real money.
 */

export type TradingMode = "simulation" | "paper" | "live";

const STORAGE_KEY = "trading:account-mode";

type Listener = (mode: TradingMode) => void;
const listeners = new Set<Listener>();

function load(): TradingMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === "simulation" ? "simulation" : "paper";
  } catch {
    return "paper";
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
    // Live is deliberately remembered as paper -- see the module comment.
    localStorage.setItem(STORAGE_KEY, next === "live" ? "paper" : next);
  } catch {
    // Works for this session, just not remembered next time.
  }
  listeners.forEach((fn) => fn(mode));
}

export function subscribeTradingMode(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Rewrites a /trading/... API path for the current mode. Paper is the
 * unprefixed router; the other two are mounted under their own prefix on
 * the backend (see app/main.py). */
export function tradingPath(path: string): string {
  if (mode === "simulation") return path.replace(/^\/trading\//, "/trading/sim/");
  if (mode === "live") return path.replace(/^\/trading\//, "/trading/live/");
  return path;
}

/** What the user has to type into a live-mode dialog. Mirrors
 * app/trading/guards.LIVE_CONFIRMATION; the backend checks it too. */
export const LIVE_CONFIRMATION = "LIVE";

/** Whether a dialog's typed confirmation satisfies the current mode --
 * only live asks for anything. */
export function liveConfirmed(current: TradingMode, typed: string): boolean {
  return current !== "live" || typed.trim() === LIVE_CONFIRMATION;
}

/** The badge every trading surface shows, derived from the mode alone --
 * the one thing on those panels that must never be ambiguous. */
export function modeBadge(current: TradingMode): {
  className: TradingMode;
  label: string;
  confirmLine: string;
} {
  if (current === "simulation") {
    return {
      className: "simulation",
      label: "SIMULATION",
      confirmLine: "SIMULATION — practice action, no real order placed",
    };
  }
  if (current === "live") {
    return { className: "live", label: "LIVE", confirmLine: "LIVE — real money" };
  }
  return { className: "paper", label: "PAPER", confirmLine: "PAPER — simulated account" };
}
