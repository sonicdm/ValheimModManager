import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

export default function LoginPage() {
  const { auth, loading, login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && auth?.authenticated) return <Navigate to="/" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md rounded-2xl border border-bark/10 bg-paper/90 p-8 shadow-sm"
      >
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-moss-deep/80">
          Trusted local network
        </p>
        <h1 className="font-display mt-2 text-4xl text-ink">Valheim Mod Manager</h1>
        <p className="mt-2 text-sm text-bark/70">
          Sign in to manage BepInEx plugins for your dedicated server.
        </p>
        <label className="mt-6 block text-sm">
          <span className="text-bark/80">Username</span>
          <input
            className="mt-1 w-full rounded-md border border-bark/20 bg-white px-3 py-2"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label className="mt-4 block text-sm">
          <span className="text-bark/80">Password</span>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-bark/20 bg-white px-3 py-2"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && <p className="mt-3 text-sm text-danger">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-md bg-moss px-4 py-2.5 font-medium text-on-moss hover:brightness-95 disabled:opacity-60"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
