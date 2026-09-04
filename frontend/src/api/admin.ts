/** /api/admin/* -- user management, admins only (backend app.routers.admin). */

import { API_BASE, checkUnauthorized, extractErrorMessage, getJson } from "./http";

export interface ManagedUser {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  /** Which broker accounts have keys, with the key id's last characters. */
  broker: Partial<Record<"paper" | "live", string>>;
}

export function listUsers(): Promise<{ users: ManagedUser[] }> {
  return getJson<{ users: ManagedUser[] }>("/admin/users");
}

async function send<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  checkUnauthorized(res);
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res, `${method} ${path} failed: ${res.status}`));
  }
  return (await res.json()) as T;
}

export function createUser(body: {
  username: string;
  display_name: string;
  password: string;
  is_admin: boolean;
}): Promise<{ user: ManagedUser }> {
  return send<{ user: ManagedUser }>("POST", "/admin/users", body);
}

export function updateUser(
  id: number,
  body: { display_name?: string; is_admin?: boolean; password?: string },
): Promise<{ user: ManagedUser }> {
  return send<{ user: ManagedUser }>("PATCH", `/admin/users/${id}`, body);
}

export function deleteUser(id: number): Promise<{ deleted: number }> {
  return send<{ deleted: number }>("DELETE", `/admin/users/${id}`);
}
