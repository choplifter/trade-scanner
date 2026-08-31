/** REST calls for backend/app/routers/replay.py. Every call that returns a
 * ReplayStateResponse also updates api/replayMode.ts's singleton, so the
 * rest of the app (ReplayPanel, useReplayFeed) sees the new session state
 * without each caller having to remember to sync it manually.
 */

import { deleteJson, getJson, patchJson, postJson } from "./http";
import { setReplaySession } from "./replayMode";
import type { Bar, IndicatorResult } from "../types/alpaca";
import type { ReplayStateResponse } from "../types/replay";

function sync(res: ReplayStateResponse): ReplayStateResponse {
  setReplaySession(res.session);
  return res;
}

/** `symbols` empty (or omitted) replays the real historical "stocks in
 * play" for startDate instead of a hand-picked list -- see
 * backend/app/routers/replay.py's /start and
 * ScannerHistoryStore.symbols_for_date. */
export function startReplay(
  startDate: string,
  symbols: string[] = [],
  speed = 1,
): Promise<ReplayStateResponse> {
  return postJson<ReplayStateResponse>("/replay/start", {
    symbols,
    start_date: startDate,
    speed,
  }).then(sync);
}

export function getReplayState(): Promise<ReplayStateResponse> {
  return getJson<ReplayStateResponse>("/replay/state").then(sync);
}

/** Same as getReplayState, but resolves to null instead of throwing when
 * the user has no session -- the expected, common case (most users aren't
 * replaying), used to hydrate api/replayMode.ts on mount without every
 * caller needing its own try/catch. */
export async function getReplayStateOrNull(): Promise<ReplayStateResponse | null> {
  try {
    return await getReplayState();
  } catch {
    setReplaySession(null);
    return null;
  }
}

export function playReplay(): Promise<ReplayStateResponse> {
  return postJson<ReplayStateResponse>("/replay/play").then(sync);
}

export function pauseReplay(): Promise<ReplayStateResponse> {
  return postJson<ReplayStateResponse>("/replay/pause").then(sync);
}

export function seekReplay(asOf: string): Promise<ReplayStateResponse> {
  return postJson<ReplayStateResponse>("/replay/seek", { as_of: asOf }).then(sync);
}

export function setReplaySpeed(speed: number): Promise<ReplayStateResponse> {
  return patchJson<ReplayStateResponse>("/replay/speed", { speed }).then(sync);
}

/** `symbol`'s 5-minute bars clipped to the session's current as_of -- see
 * ReplayEngine.bars_up_to. The server does the clipping, not this call:
 * there's nothing here to slice a "future" bar off of even transiently. */
export function getReplayBars(symbol: string): Promise<{ symbol: string; bars: Bar[] }> {
  return getJson<{ symbol: string; bars: Bar[] }>(`/replay/bars/${encodeURIComponent(symbol)}`);
}

export interface ReplayIndicatorsResponse {
  symbol: string;
  vwap: (number | null)[];
  vwap_premarket: (number | null)[];
  indicators: IndicatorResult[];
}

/** VWAP + the same reference-line/overlay indicators the live chart draws,
 * computed against this replay session's bars -- see
 * backend/app/routers/replay.py's /indicators/{symbol}. Kept as its own
 * call rather than folded into getReplayBars: this one costs real Alpaca
 * calls for weekly/monthly/hourly bars, so it doesn't need to be refetched
 * on every clock tick as eagerly as the candles themselves. */
export function getReplayIndicators(symbol: string): Promise<ReplayIndicatorsResponse> {
  return getJson<ReplayIndicatorsResponse>(`/replay/indicators/${encodeURIComponent(symbol)}`);
}

export function stopReplay(): Promise<{ stopped: boolean }> {
  return deleteJson<{ stopped: boolean }>("/replay/stop").then((res) => {
    setReplaySession(null);
    return res;
  });
}
