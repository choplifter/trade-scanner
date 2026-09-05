import { useEffect, useRef, useState } from "react";

import { optionEvents } from "../api/options";
import type { OptionEventsResponse } from "../types/options";

export interface OptionEventsState {
  events: OptionEventsResponse | null;
  loading: boolean;
  error: string | null;
}

/**
 * What is scheduled inside a symbol's option expiries (earnings, macro
 * releases) and where its ATM IV sits in its history -- see backend
 * app/options/events.py. Refetched when the symbol changes, and when the
 * chain's ATM IV moves by a whole percentage point (the rank is measured
 * against it); finer moves would refetch on every quote.
 */
export function useOptionEvents(symbol: string | null, atmIv: number | null, dte: number | null): OptionEventsState {
  const [state, setState] = useState<OptionEventsState>({ events: null, loading: false, error: null });
  const seqRef = useRef(0);
  const ivKey = atmIv == null ? "" : atmIv.toFixed(2);
  // The expiry the IV belongs to, for the record; a change of expiry alone
  // does not refetch (the rank is the same reading against the same history).
  const dteRef = useRef<number | null>(dte);
  dteRef.current = dte;

  useEffect(() => {
    if (!symbol) {
      setState({ events: null, loading: false, error: null });
      return;
    }
    const seq = ++seqRef.current;
    setState((s) => ({ events: s.events?.underlying === symbol ? s.events : null, loading: true, error: null }));
    optionEvents(symbol, ivKey === "" ? null : Number(ivKey), dteRef.current)
      .then((events) => {
        if (seq === seqRef.current) setState({ events, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (seq === seqRef.current) {
          setState((s) => ({ events: s.events, loading: false, error: err instanceof Error ? err.message : String(err) }));
        }
      });
  }, [symbol, ivKey]);

  return state;
}
