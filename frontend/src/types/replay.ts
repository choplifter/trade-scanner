import type { ScannerRow } from "./alpaca";

/** Mirrors backend/app/replay/store.py's replay_sessions row (via
 * ReplayStore._row_to_session) -- one active session per logged-in user. */
export interface ReplaySession {
  user_id: number;
  symbols: string[];
  start_date: string;
  end_date: string;
  lookback_days: number;
  as_of: string;
  playing: boolean;
  speed: number;
  created_at: string;
  updated_at: string;
}

export interface ReplayRange {
  start: string | null;
  end: string | null;
}

/** Response shape shared by every /api/replay/* endpoint except /stop --
 * see backend/app/routers/replay.py's _state_payload. `views` is null only
 * when the session exists but its ReplayEngine isn't resident (e.g. right
 * after a server restart, before the pacing loop or another request has
 * rebuilt it) -- see app.replay.engine.ReplayEngineCache. */
export interface ReplayStateResponse {
  session: ReplaySession;
  range: ReplayRange;
  views: Record<string, ScannerRow[]> | null;
}

/** What /ws/replay pushes -- the counterpart to ScannerUpdateMessage, but
 * per-user (topic includes the user's own id -- see
 * app.replay.loop.topic_for) and reduced-fidelity (see ReplayEngine's
 * module docstring for what a replayed row can't carry: news, momentum
 * alarm, strategy signals, fundamentals, spread %). */
export interface ReplayUpdateMessage {
  type: "replay_update";
  scanner: string;
  as_of: string;
  rows: ScannerRow[];
}
