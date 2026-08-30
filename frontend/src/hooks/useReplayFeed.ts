import { useEffect, useState } from "react";

import { updateReplayAsOf } from "../api/replayMode";
import { replaySocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";

export interface ReplayFeedState {
  rows: ScannerRow[];
  /** The replayed clock position these rows were computed at -- null until
   * the first message arrives (e.g. socket still connecting). */
  asOf: string | null;
  loading: boolean;
}

/**
 * Subscribes to one of the three replayed views ("gainers" | "losers" |
 * "most_active") over /ws/replay -- the replay counterpart to
 * useScannerFeed, but topic-only: there's no REST fetch-then-subscribe
 * step here because ws/replay_ws.py already sends an initial snapshot on
 * subscribe (see its module docstring), and no session/window/momentum
 * metadata because a replayed row never carries momentum_pct or a
 * strategy signal -- see app.replay.engine's module docstring for the
 * full list of what reduced fidelity means here.
 *
 * `active` gates the subscription the same way useScannerFeed's `scanner`
 * param does (null = idle) -- callers pass it as `!active ? null : scanner`.
 */
export function useReplayFeed(scanner: string | null): ReplayFeedState {
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (scanner === null) return;
    setLoading(true);
    const unsubscribe = replaySocket.subscribe(scanner, (msg) => {
      setRows(msg.rows);
      setAsOf(msg.as_of);
      // Keeps api/replayMode.ts's singleton current for every other
      // consumer (the seek slider, ChartWidget's replay-clipped bars) --
      // see updateReplayAsOf's own docstring.
      updateReplayAsOf(msg.as_of);
      setLoading(false);
    });
    return unsubscribe;
  }, [scanner]);

  return { rows, asOf, loading };
}
