/** The logged-in user, readable outside the React tree (module singleton
 * like api/tradingMode.ts): widgets deep in the dock ask "is this an
 * admin?" without a prop chain. App.tsx sets it whenever auth changes. */

import { useEffect, useState } from "react";

import type { User } from "./auth";

type Listener = (user: User | null) => void;
const listeners = new Set<Listener>();
let current: User | null = null;

export function getCurrentUser(): User | null {
  return current;
}

export function setCurrentUser(user: User | null): void {
  current = user;
  listeners.forEach((fn) => fn(user));
}

export function useCurrentUser(): User | null {
  const [user, setUser] = useState<User | null>(current);
  useEffect(() => {
    listeners.add(setUser);
    return () => {
      listeners.delete(setUser);
    };
  }, []);
  return user;
}
