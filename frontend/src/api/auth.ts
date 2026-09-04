const API_BASE = "/api";

export interface User {
  id: number;
  username: string;
  display_name: string;
  /** The operator: broker calls may fall back to the Alpaca keys in
   * backend/.env. Everyone else connects their own account in Settings. */
  is_admin?: boolean;
}

async function extractErrorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export async function login(username: string, password: string): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, "Login failed"));
  }
  const body = (await res.json()) as { user: User };
  return body.user;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
}

/** Never throws -- {user: null} is a normal, expected answer (not logged
 * in), not a failure. See routers/auth.py's /me for why this is a 200
 * either way rather than a 401 the caller would have to special-case. */
export async function getMe(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: "include" });
  if (!res.ok) return null;
  const body = (await res.json()) as { user: User | null };
  return body.user;
}
