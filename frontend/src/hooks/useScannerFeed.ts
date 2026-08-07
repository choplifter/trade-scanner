import { useEffect, useState } from "react";

import { getScanner } from "../api/http";
import { scannerSocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";

export interface ScannerFeedState {
  rows: ScannerRow[];
  session: string;
  loading: boolean;
}

export function useScannerFeed(scanner: string): ScannerFeedState {
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [session, setSession] = useState("closed");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    getScanner(scanner)
      .then((res) => {
        if (cancelled) return;
        setRows(res.rows);
        setSession(res.session);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    const unsubscribe = scannerSocket.subscribe(scanner, (msg) => {
      setRows(msg.rows);
      setSession(msg.session);
      setLoading(false);
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [scanner]);

  return { rows, session, loading };
}
