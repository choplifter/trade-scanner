/** Mirrors the logged-in user's replay session, kept in sync by
 * api/replay.ts after every /api/replay/* call. A module-level singleton
 * with its own pub/sub, same spirit as api/tradingMode.ts -- other code
 * (the ReplayPanel, useReplayFeed) needs to read "is a session active"
 * without threading it through React props.
 *
 * "Active" means a session exists at all, playing or paused -- pausing
 * only stops the clock advancing, it doesn't mean "back to live." See
 * backend/app/routers/trading_sim.py's _replay_seam for why the backend
 * makes the same distinction for order pricing.
 *
 * Not persisted client-side (unlike tradingMode's localStorage) -- the
 * backend is the source of truth (ReplayStore); a page load hydrates this
 * from GET /api/replay/state instead (see ReplayPanel's mount effect).
 */

import type { ReplaySession } from "../types/replay";

type Listener = (session: ReplaySession | null) => void;
const listeners = new Set<Listener>();

let session: ReplaySession | null = null;

export function getReplaySession(): ReplaySession | null {
  return session;
}

export function isReplayActive(): boolean {
  return session !== null;
}

export function setReplaySession(next: ReplaySession | null): void {
  session = next;
  listeners.forEach((fn) => fn(session));
}

export function subscribeReplaySession(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
