import { useCallback, useEffect, useState } from "react";

import { createUser, deleteUser, listUsers, updateUser, type ManagedUser } from "../../api/admin";

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function brokerLabel(user: ManagedUser): string {
  const parts: string[] = [];
  if (user.broker.paper) parts.push(`paper …${user.broker.paper}`);
  if (user.broker.live) parts.push(`live …${user.broker.live}`);
  return parts.length > 0 ? parts.join(" · ") : user.is_admin ? "operator's .env keys" : "none";
}

/** Settings → Users, admins only: every login, its role and broker
 * status, plus create / rename / reset password / promote / delete. Same
 * operations as scripts/create_user.py, through app.routers.admin. */
export function UsersTab({ currentUserId }: { currentUserId: number }) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ kind: "password" | "delete"; user: ManagedUser } | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [form, setForm] = useState({ username: "", display_name: "", password: "", is_admin: false });

  const load = useCallback(() => {
    listUsers()
      .then((res) => {
        setUsers(res.users);
        setError(null);
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      load();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const canCreate = form.username.trim().length >= 3 && form.display_name.trim().length > 0 && form.password.length >= 8;

  return (
    <div className="settings-section users-tab">
      <p className="order-hint">
        Every login is its own account: own broker keys (Settings → Broker), own journal, own simulation.
        Admins may trade on the operator's .env keys and manage users here.
      </p>
      {error && <p className="order-rejection">{error}</p>}

      <table className="performance-table users-table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Name</th>
            <th>Admin</th>
            <th>Broker keys</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const me = u.id === currentUserId;
            return (
              <tr key={u.id}>
                <td className="symbol-cell">
                  {u.username}
                  {me ? <span className="order-hint"> (you)</span> : ""}
                </td>
                <td>{u.display_name}</td>
                <td>
                  <input
                    type="checkbox"
                    checked={u.is_admin}
                    disabled={busy || me}
                    title={me ? "You cannot change your own admin flag" : "Admin: may trade on the operator's .env keys and manage users"}
                    onChange={(e) => void run(() => updateUser(u.id, { is_admin: e.target.checked }))}
                  />
                </td>
                <td>{brokerLabel(u)}</td>
                <td className="row-actions">
                  <button
                    type="button"
                    className="row-action"
                    disabled={busy}
                    onClick={() => {
                      setNewPassword("");
                      setPending({ kind: "password", user: u });
                    }}
                  >
                    Reset password
                  </button>{" "}
                  <button
                    type="button"
                    className="row-action"
                    disabled={busy || me}
                    title={me ? "You cannot delete your own login" : `Delete ${u.username}`}
                    onClick={() => setPending({ kind: "delete", user: u })}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {pending?.kind === "password" && (
        <div className="users-inline">
          <span>
            New password for <strong>{pending.user.username}</strong>
          </span>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            placeholder="at least 8 characters"
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <button
            type="button"
            className="generate-button"
            disabled={busy || newPassword.length < 8}
            onClick={() =>
              void run(() => updateUser(pending.user.id, { password: newPassword })).then((ok) => {
                if (ok) setPending(null);
              })
            }
          >
            Set password
          </button>
          <button type="button" className="row-action" onClick={() => setPending(null)}>
            Cancel
          </button>
        </div>
      )}

      {pending?.kind === "delete" && (
        <div className="users-inline">
          <span>
            Delete <strong>{pending.user.username}</strong> ({pending.user.display_name})? Their login and broker
            keys go; journal and simulation rows stay orphaned.
          </span>
          <button
            type="button"
            className="generate-button live-action"
            disabled={busy}
            onClick={() =>
              void run(() => deleteUser(pending.user.id)).then((ok) => {
                if (ok) setPending(null);
              })
            }
          >
            Delete user
          </button>
          <button type="button" className="row-action" onClick={() => setPending(null)}>
            Keep
          </button>
        </div>
      )}

      <div className="users-create">
        <span className="option-orders-title">New login</span>
        <div className="users-create-form">
          <label>
            Username
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              value={form.username}
              placeholder="lowercase, 3-32 characters"
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </label>
          <label>
            Display name
            <input
              type="text"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="new-password"
              value={form.password}
              placeholder="at least 8 characters"
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </label>
          <label className="users-check">
            <input
              type="checkbox"
              checked={form.is_admin}
              onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
            />{" "}
            Admin
          </label>
          <button
            type="button"
            className="generate-button"
            disabled={busy || !canCreate}
            onClick={() =>
              void run(() =>
                createUser({
                  username: form.username.trim().toLowerCase(),
                  display_name: form.display_name.trim(),
                  password: form.password,
                  is_admin: form.is_admin,
                }),
              ).then((ok) => {
                if (ok) setForm({ username: "", display_name: "", password: "", is_admin: false });
              })
            }
          >
            Create
          </button>
        </div>
        <p className="order-hint">
          Tell the new user their password; they connect their own Alpaca keys under Settings → Broker.
        </p>
      </div>
    </div>
  );
}
