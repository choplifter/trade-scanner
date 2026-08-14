import { useEffect, useRef, useState } from "react";

import { scannerSocket } from "../api/ws";
import type { ScannerRow } from "../types/alpaca";

const VIEWS = ["gainers", "losers", "most_active"] as const;
const STORAGE_KEY = "alarms:enabled";

export interface AlarmEntry {
  row: ScannerRow;
  /** Client-side timestamp (ms) of when this symbol first became flagged
   * -- not from the backend, which doesn't track "since when" for a
   * boolean flag. Used only to order/label alarms in this session. */
  firstSeenAt: number;
}

export interface AlarmsState {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  alarms: AlarmEntry[];
  overlayOpen: boolean;
  openOverlay: () => void;
  closeOverlay: () => void;
}

function loadEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/**
 * Watches every ranked scanner view's live feed for rows flagged
 * is_momentum_alert (a fast, wick-less move -- see backend
 * app.scanners.formulas.is_momentum_alert) and surfaces them as a
 * dashboard-wide alarm, independent of whatever symbol's chart happens to
 * be open. This is why it's a scanner-side flag rather than a chart
 * indicator: an indicator only ever runs for the one symbol currently
 * loaded in ChartWidget, so it could never notice a breakout on a symbol
 * you aren't already looking at. Off by default.
 */
export function useAlarms(): AlarmsState {
  const [enabled, setEnabledState] = useState(loadEnabled);
  const [alarms, setAlarms] = useState<AlarmEntry[]>([]);
  const [overlayOpen, setOverlayOpen] = useState(false);
  // Latest flagged rows per view, so a symbol dropping out of (or no
  // longer flagged in) whichever view last reported it correctly clears --
  // three views can each independently flag/unflag the same symbol.
  const perView = useRef<Record<string, Map<string, ScannerRow>>>({
    gainers: new Map(),
    losers: new Map(),
    most_active: new Map(),
  });
  const firstSeenAt = useRef<Map<string, number>>(new Map());

  function setEnabled(value: boolean) {
    setEnabledState(value);
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch {
      // Private browsing / storage disabled -- the toggle still works for
      // this session, it just won't be remembered next time.
    }
  }

  useEffect(() => {
    if (!enabled) {
      perView.current = { gainers: new Map(), losers: new Map(), most_active: new Map() };
      firstSeenAt.current = new Map();
      setAlarms([]);
      setOverlayOpen(false);
      return;
    }

    const unsubscribes = VIEWS.map((view) =>
      scannerSocket.subscribe(view, (msg) => {
        const flagged = new Map<string, ScannerRow>();
        for (const row of msg.rows) {
          if (row.is_momentum_alert) flagged.set(row.symbol, row);
        }
        perView.current[view] = flagged;

        const merged = new Map<string, ScannerRow>();
        for (const v of VIEWS) {
          for (const [symbol, row] of perView.current[v]) merged.set(symbol, row);
        }

        for (const symbol of firstSeenAt.current.keys()) {
          if (!merged.has(symbol)) firstSeenAt.current.delete(symbol);
        }
        let sawNewTrigger = false;
        for (const symbol of merged.keys()) {
          if (!firstSeenAt.current.has(symbol)) {
            firstSeenAt.current.set(symbol, Date.now());
            sawNewTrigger = true;
          }
        }

        setAlarms(
          Array.from(merged.entries())
            .map(([symbol, row]) => ({ row, firstSeenAt: firstSeenAt.current.get(symbol)! }))
            .sort((a, b) => b.firstSeenAt - a.firstSeenAt),
        );
        if (sawNewTrigger) setOverlayOpen(true);
      }),
    );

    return () => unsubscribes.forEach((unsub) => unsub());
  }, [enabled]);

  return {
    enabled,
    setEnabled,
    alarms,
    overlayOpen,
    openOverlay: () => setOverlayOpen(true),
    closeOverlay: () => setOverlayOpen(false),
  };
}
