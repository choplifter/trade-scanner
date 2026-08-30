import { useEffect, useState } from "react";

import { getReplaySession, subscribeReplaySession } from "../api/replayMode";
import type { ReplaySession } from "../types/replay";

/** Thin React view over api/replayMode.ts's singleton, same pattern as
 * useTradingMode over api/tradingMode.ts -- the state lives in the
 * singleton (so useReplayFeed/api/replay.ts can read it outside React),
 * this just re-renders whenever it changes. */
export function useReplaySession(): ReplaySession | null {
  const [session, setSession] = useState<ReplaySession | null>(getReplaySession);
  useEffect(() => subscribeReplaySession(setSession), []);
  return session;
}
