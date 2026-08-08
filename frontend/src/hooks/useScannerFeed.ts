import { useEffect, useState } from "react";

import { getScanner } from "../api/http";
import { scannerSocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";
import { generateMockRows } from "../utils/mockScannerData";

export interface ScannerFeedState {
  rows: ScannerRow[];
  session: string;
  loading: boolean;
}

const MOCK_REFRESH_MS = 4000;

export function useScannerFeed(scanner: string, mock = false): ScannerFeedState {
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [session, setSession] = useState("closed");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (mock) {
      setLoading(false);
      setSession("regular");
      setRows(generateMockRows());
      const interval = setInterval(() => setRows(generateMockRows()), MOCK_REFRESH_MS);
      return () => clearInterval(interval);
    }

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
  }, [scanner, mock]);

  return { rows, session, loading };
}
