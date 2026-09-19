import { useCallback, useEffect, useRef, useState } from "react";

import { daysUntil, getChain, getExpiries } from "../api/options";
import { subscribeReplaySession } from "../api/replayMode";
import { useReplaySession } from "./useReplaySession";
import type { ChainResponse, ExpiryInfo } from "../types/options";

/** Matches the backend's chain TTL (app/options/chain_fetch.py): polling
 * faster would just be served the same cached chain. */
const CHAIN_POLL_MS = 15_000;
/** In a replay the chain moves with the clock: one refetch per tick, a
 * short debounce so a fast playback does not stack requests. */
const REPLAY_DEBOUNCE_MS = 250;

export interface OptionChainState {
  expiries: ExpiryInfo[];
  expiry: string | null;
  setExpiry: (expiry: string) => void;
  chain: ChainResponse | null;
  spot: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /** Make sure `expiry` is in the strip: true when it already is, else the
   * far strip (beyond the picker's window) is fetched and merged in, and
   * the answer says whether it was there. */
  ensureExpiry: (expiry: string) => Promise<boolean>;
}

/** The picker's data: the expiry strip once per symbol (the nearest expiry
 * with at least a day left is preselected -- 0DTE only when there is
 * nothing else), and the selected expiry's chain re-polled while mounted
 * and refetched on every replay tick. Off entirely when `enabled` is false
 * (no symbol, or a calendar's second chain while no calendar is picked). */
export function useOptionChain(underlying: string | null, enabled: boolean): OptionChainState {
  // The strip belongs to a date: in a replay it is the replayed one, and a
  // replay that starts (or runs past midnight) has to fetch it again.
  // Without this the picker kept today's expiries under a chain priced
  // weeks earlier -- the dates the ticket then traded were the wrong ones.
  const replayDay = useReplaySession()?.as_of?.slice(0, 10) ?? null;
  const [expiries, setExpiries] = useState<ExpiryInfo[]>([]);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [chain, setChain] = useState<ChainResponse | null>(null);
  const [spot, setSpot] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const cancelledRef = useRef(false);

  // What is selected right now, read inside the fetch below without making
  // it a dependency (that would refetch the strip on every pick).
  const expiryRef = useRef<string | null>(null);
  expiryRef.current = expiry;
  // The replay day the strip was last fetched for, to tell a step inside a
  // replay from entering or leaving one.
  const stripDayRef = useRef<string | null>(null);

  useEffect(() => {
    setExpiries([]);
    setChain(null);
    setSpot(null);
    setError(null);
    const previousStripDay = stripDayRef.current;
    stripDayRef.current = replayDay;
    if (!underlying || !enabled) return;
    cancelledRef.current = false;
    setLoading(true);
    getExpiries(underlying, { board: true })
      .then((res) => {
        if (cancelledRef.current) return;
        setExpiries(res.expiries);
        setSpot(res.spot);
        // Stepping the replay clock within a replay keeps the expiry
        // already picked, as long as it still trades then -- a day's step
        // should not rip the strike board away mid-trade. Entering or
        // leaving a replay does not: arriving in August with September
        // still selected is the chain of a date the replay has not reached
        // (it read "40d" on the picker). Then, and on a fresh symbol, the
        // nearest expiry with a day left is taken, 0DTE only when there is
        // nothing else.
        const steppedWithinReplay = previousStripDay !== null && replayDay !== null;
        const held = expiryRef.current;
        const kept = steppedWithinReplay && held && res.expiries.some((e) => e.expiry === held) ? held : null;
        const preferred = kept ?? (res.expiries.find((e) => e.dte >= 1) ?? res.expiries[0])?.expiry ?? null;
        setExpiry(preferred);
        if (!preferred) setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelledRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelledRef.current = true;
    };
  }, [underlying, enabled, replayDay]);

  // A different symbol drops the pick; a replay date change keeps it (see
  // the strip effect above).
  useEffect(() => {
    setExpiry(null);
  }, [underlying]);

  useEffect(() => {
    if (!underlying || !enabled || !expiry) return;
    let cancelled = false;
    const load = () => {
      getChain(underlying, expiry)
        .then((res) => {
          if (cancelled) return;
          setChain(res);
          setSpot(res.spot);
          setError(null);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        });
    };
    setLoading(true);
    load();
    const timer = setInterval(load, CHAIN_POLL_MS);
    let debounce: number | null = null;
    const unsubscribe = subscribeReplaySession(() => {
      if (debounce != null) window.clearTimeout(debounce);
      debounce = window.setTimeout(load, REPLAY_DEBOUNCE_MS);
    });
    return () => {
      cancelled = true;
      clearInterval(timer);
      unsubscribe();
      if (debounce != null) window.clearTimeout(debounce);
    };
  }, [underlying, enabled, expiry, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const ensureExpiry = useCallback(
    async (wanted: string): Promise<boolean> => {
      if (!underlying) return false;
      if (expiries.some((e) => e.expiry === wanted)) return true;
      // Counted from the replayed day when there is one: the far strip is
      // fetched by a distance in days, and today's distance is meaningless
      // in a replay.
      const dte = daysUntil(wanted, replayDay);
      const res = await getExpiries(underlying, { far: { from: dte, to: dte } });
      if (cancelledRef.current) return false;
      const merged = [...expiries];
      for (const e of res.expiries) {
        if (!merged.some((m) => m.expiry === e.expiry)) merged.push(e);
      }
      merged.sort((a, b) => a.expiry.localeCompare(b.expiry));
      setExpiries(merged);
      return merged.some((e) => e.expiry === wanted);
    },
    [underlying, expiries, replayDay],
  );

  return { expiries, expiry, setExpiry, chain, spot, loading, error, refresh, ensureExpiry };
}
