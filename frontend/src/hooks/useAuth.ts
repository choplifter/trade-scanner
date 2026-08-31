import { useCallback, useEffect, useState } from "react";

import { getMe, login as apiLogin, logout as apiLogout, type User } from "../api/auth";
import { onUnauthorized } from "../api/http";

export interface AuthState {
  user: User | null;
  /** True only during the initial getMe() check on mount -- distinct from
   * "logged out", so App can show a blank/loading beat instead of
   * flashing the login page for a session that turns out to be valid. */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((found) => {
        if (!cancelled) setUser(found);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A 401 from any authenticated call means the session the browser is
  // sending is no longer valid -- clearing `user` sends App back to
  // LoginPage instead of leaving widgets stuck retrying against a session
  // that will never come back on its own (see api/http.ts's onUnauthorized).
  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const login = useCallback(async (username: string, password: string) => {
    const found = await apiLogin(username, password);
    setUser(found);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return { user, loading, login, logout };
}
