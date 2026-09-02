import { useCallback, useEffect, useState } from "react";

import { getJournalEntries, getTrades, saveJournalEntry } from "../api/http";
import type { JournalEntry } from "../types/journal";
import type { Trade } from "../types/trading";
import { useTradingMode } from "./useTradingMode";

export interface JournaledTrade extends Trade {
  journal: JournalEntry | null;
}

interface JournalState {
  rows: JournaledTrade[];
  loading: boolean;
  error: string | null;
  saveEntry: (tradeId: string, body: { note: string; rating: number | null; tags: string[] }) => Promise<void>;
}

/** Closed trades merged with this user's journal notes on them -- see
 * frontend/src/api/http.ts's getTrades/getJournalEntries. getTrades() is
 * unchanged from what TradingWidget already uses: it's already routed
 * through tradingPath() (api/tradingMode.ts), so it returns Simulation
 * Mode's per-user trades whenever that mode is active, and the one shared
 * real account's trades otherwise. `mode` is still a dependency of the
 * fetch effect below, though -- tradingPath() reads the mode singleton at
 * *call* time, so switching modes after the initial fetch would otherwise
 * leave this hook showing whichever trade list was current on mount. */
export function useJournal(): JournalState {
  const { mode } = useTradingMode();
  const [trades, setTrades] = useState<Trade[]>([]);
  const [entries, setEntries] = useState<Record<string, JournalEntry>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getTrades("all"), getJournalEntries()])
      .then(([tradesRes, journalRes]) => {
        if (cancelled) return;
        setTrades(tradesRes.trades);
        setEntries(journalRes.entries);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load journal");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  const saveEntry = useCallback(
    async (tradeId: string, body: { note: string; rating: number | null; tags: string[] }) => {
      const { entry } = await saveJournalEntry(tradeId, body);
      setEntries((current) => ({ ...current, [tradeId]: entry }));
    },
    [],
  );

  const rows: JournaledTrade[] = trades.map((t) => ({ ...t, journal: entries[t.id] ?? null }));

  return { rows, loading, error, saveEntry };
}
