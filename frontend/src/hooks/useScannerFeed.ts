import { useEffect, useState } from "react";

import { getScanner } from "../api/http";
import { scannerSocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";

export interface ScannerFeedState {
  rows: ScannerRow[];
  session: string;
  loading: boolean;
  /** True when there's nothing live (e.g. markets closed) and rows are the
   * most recently completed session's real data instead. */
  isLatestSession: boolean;
}

/**
 * Subscribes to a named, server-computed view.
 *
 * `null` leaves it unsubscribed and idle -- the unified scanner widget calls
 * this and useScreenFeed unconditionally (hooks can't be conditional) and
 * feeds whichever one its active tab doesn't need a null, so only one feed is
 * ever actually connected.
 */
export function useScannerFeed(scanner: string | null): ScannerFeedState {
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [session, setSession] = useState("closed");
  const [loading, setLoading] = useState(true);
  const [isLatestSession, setIsLatestSession] = useState(false);

  useEffect(() => {
    if (scanner === null) return;
    let cancelled = false;
    setLoading(true);

    getScanner(scanner)
      .then((res) => {
        if (cancelled) return;
        setRows(res.rows);
        setSession(res.session);
        setIsLatestSession(res.is_latest_session);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    const unsubscribe = scannerSocket.subscribe(scanner, (msg) => {
      setRows(msg.rows);
      setSession(msg.session);
      setIsLatestSession(msg.is_latest_session);
      setLoading(false);
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [scanner]);

  return { rows, session, loading, isLatestSession };
}
