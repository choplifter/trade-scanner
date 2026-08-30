import { useState } from "react";
import type { FormEvent } from "react";

interface LoginPageProps {
  onLogin: (username: string, password: string) => Promise<void>;
}

/** The only screen reachable without a session -- see App.tsx. No signup
 * link, no "forgot password": accounts are created with
 * `python -m scripts.create_user` by whoever administers this deployment. */
export function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onLogin(username, password);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={(e) => void handleSubmit(e)}>
        <h1>Stocks in Play</h1>
        <label className="login-field">
          Username
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </label>
        <label className="login-field">
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="order-rejection">{error}</p>}
        <button type="submit" className="generate-button" disabled={submitting || !username || !password}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
