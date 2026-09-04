import { useCallback, useEffect, useState } from "react";

import { getJson, postJson } from "../api/http";

const POLL_MS = 20_000;

export interface ScannerStatus {
  paused: boolean;
  session: string;
}

/** The operator's scanner pause switch (backend /api/scanners/status and
 * /pause): polled slowly, refetched right after a toggle. Everyone sees
 * the state; only an admin's toggle succeeds (403 otherwise). */
export function useScannerStatus(): { status: ScannerStatus | null; setPaused: (paused: boolean) => Promise<void> } {
  const [status, setStatus] = useState<ScannerStatus | null>(null);

  const load = useCallback(() => {
    getJson<ScannerStatus>("/scanners/status")
      .then(setStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const setPaused = useCallback(async (paused: boolean) => {
    const next = await postJson<ScannerStatus>("/scanners/pause", { paused });
    setStatus(next);
  }, []);

  return { status, setPaused };
}
