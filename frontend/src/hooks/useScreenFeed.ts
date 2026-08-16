import { useEffect, useRef, useState } from "react";

import { scannerSocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";
import type { Screen } from "../types/screener";

export interface ScreenFeedState {
  rows: ScannerRow[];
  /** Derived columns (float, rank score) keyed by field then symbol -- they
   * aren't ScannerRow attributes, so they travel beside the rows. */
  derived: Record<string, Record<string, number | null>>;
  session: string;
  isLatestSession: boolean;
  totalMatched: number;
  tradableSize: number;
  universeSize: number;
  loading: boolean;
}

const EMPTY: ScreenFeedState = {
  rows: [],
  derived: {},
  session: "closed",
  isLatestSession: false,
  totalMatched: 0,
  tradableSize: 0,
  universeSize: 0,
  loading: true,
};

/**
 * Subscribes this socket to a live, user-defined screen.
 *
 * The server keeps one screen per connection and re-evaluates it on every
 * poll tick (see app/ws/screen_subscriptions.py), so a custom screen updates
 * exactly like the fixed views always have -- which is the point of merging
 * the scanner and the screener into one widget rather than having a live one
 * and a request-driven one side by side.
 *
 * `screen` is serialized for the dependency comparison rather than compared
 * by reference: it's rebuilt on every render of the widget that owns the
 * filter state, so a reference dependency would re-subscribe on each
 * keystroke.
 */
export function useScreenFeed(screen: Screen | null): ScreenFeedState {
  const [state, setState] = useState<ScreenFeedState>(EMPTY);
  const serialized = screen === null ? null : JSON.stringify(screen);
  const latest = useRef(serialized);
  latest.current = serialized;

  useEffect(() => {
    if (serialized === null) return;
    setState((prev) => ({ ...prev, loading: true }));

    const unsubscribe = scannerSocket.subscribeScreen(JSON.parse(serialized) as Screen, (msg) => {
      setState({
        rows: msg.rows,
        derived: msg.derived ?? {},
        session: msg.session,
        isLatestSession: msg.is_latest_session,
        totalMatched: msg.total_matched,
        tradableSize: msg.tradable_size,
        universeSize: msg.universe_size,
        loading: false,
      });
    });

    return unsubscribe;
  }, [serialized]);

  return state;
}
