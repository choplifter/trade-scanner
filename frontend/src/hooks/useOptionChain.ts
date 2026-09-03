import { useCallback, useEffect, useRef, useState } from "react";

import { getChain, getExpiries } from "../api/options";
import type { ChainResponse, ExpiryInfo } from "../types/options";

/** Matches the backend's chain TTL (app/options/chain_fetch.py): polling
 * faster would just be served the same cached chain. */
const CHAIN_POLL_MS = 15_000;

export interface OptionChainState {
  expiries: ExpiryInfo[];
  expiry: string | null;
  setExpiry: (expiry: string) => void;
  chain: ChainResponse | null;
  spot: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/** The picker's data: the expiry strip once per symbol (the nearest expiry
 * with at least a day left is preselected -- 0DTE only when there is
 * nothing else), and the selected expiry's chain re-polled while mounted.
 * Off entirely when `enabled` is false (Simulation mode, no symbol). */
export function useOptionChain(underlying: string | null, enabled: boolean): OptionChainState {
  const [expiries, setExpiries] = useState<ExpiryInfo[]>([]);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [chain, setChain] = useState<ChainResponse | null>(null);
  const [spot, setSpot] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const cancelledRef = useRef(false);

  useEffect(() => {
    setExpiries([]);
    setExpiry(null);
    setChain(null);
    setSpot(null);
    setError(null);
    if (!underlying || !enabled) return;
    cancelledRef.current = false;
    setLoading(true);
    getExpiries(underlying)
      .then((res) => {
        if (cancelledRef.current) return;
        setExpiries(res.expiries);
        setSpot(res.spot);
        const preferred = res.expiries.find((e) => e.dte >= 1) ?? res.expiries[0] ?? null;
        setExpiry(preferred ? preferred.expiry : null);
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
  }, [underlying, enabled]);

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
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [underlying, enabled, expiry, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  return { expiries, expiry, setExpiry, chain, spot, loading, error, refresh };
}
