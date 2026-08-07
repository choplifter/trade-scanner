import { useEffect, useState } from "react";

import { getSession } from "../api/http";

export function useMarketSession(pollMs = 30_000): string {
  const [session, setSession] = useState("closed");

  useEffect(() => {
    let cancelled = false;

    const fetchSession = () => {
      getSession()
        .then((res) => {
          if (!cancelled) setSession(res.session);
        })
        .catch(() => {});
    };

    fetchSession();
    const interval = setInterval(fetchSession, pollMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [pollMs]);

  return session;
}
