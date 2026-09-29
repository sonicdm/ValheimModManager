import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, DashboardStats, PendingUpdate } from "../api/client";
import { StatusToast, useStatusToast } from "../components/StatusToast";
import { formatLocalDateTime } from "../lib/time";

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
      <p className="text-xs font-semibold uppercase tracking-wider text-bark/60">{label}</p>
      <p className="mt-2 font-display text-4xl text-ink">{value}</p>
      {hint && <p className="mt-1 text-xs text-bark/60">{hint}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [serverStatus, setServerStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useStatusToast();

  const load = useCallback(async () => {
    setStats(await api.get<DashboardStats>("/api/dashboard"));
  }, []);

  const loadServerStatus = useCallback(async () => {
    try {
      const s = await api.get<{ configured: boolean; status?: string | null }>("/api/server/status");
      if (s.configured) setServerStatus(s.status ?? "Unknown");
      else setServerStatus(null);
    } catch {
      setServerStatus("error: status unavailable");
    }
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
    loadServerStatus();
  }, [load, loadServerStatus]);

  useEffect(() => {
    function onScanDone() {
      load().catch(() => undefined);
    }
    window.addEventListener("vmm:scan-done", onScanDone);
    return () => window.removeEventListener("vmm:scan-done", onScanDone);
  }, [load]);

  async function scan() {
    setBusy(true);
    setError(null);
    toast.showBusy("Scanning plugins…");
    try {
      await api.post("/api/scan");
      await load();
      toast.showOk("Plugin scan finished.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Scan failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function checkUpdates() {
    setBusy(true);
    setError(null);
    toast.showBusy("Refreshing store indexes and checking for updates…");
    try {
      const pending = await api.post<PendingUpdate[]>("/api/updates/check");
      await load();
      if (pending.length === 0) {
        toast.showOk("Indexes checked — no updates available.");
      } else {
        toast.showOk(
          pending.length === 1
            ? `Found 1 update: ${pending[0].full_name}.`
            : `Found ${pending.length} updates.`,
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Update check failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    setBusy(true);
    setError(null);
    toast.showBusy("Restarting server (stop → bootstrap sync → start)…");
    try {
      await api.post("/api/server/restart");
      await loadServerStatus();
      await load();
      toast.showOk("Restart + sync finished.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Restart failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  if (!stats) {
    return <p className="text-bark/70">{error || "Loading dashboard…"}</p>;
  }

  const statusText = serverStatus ?? stats.supervisor_status;
  const online =
    statusText === "RUNNING"
      ? "Online"
      : stats.supervisor_configured
        ? statusText || "Checking…"
        : "Supervisor off";

  return (
    <div className="space-y-8">
      <StatusToast message={toast.message} tone={toast.tone} busy={toast.busy} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-bark/70">Server status</p>
          <p className="font-display text-3xl text-ink">{online}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={busy} onClick={scan} className="btn-primary">
            {busy && toast.message?.startsWith("Scanning") ? "Scanning…" : "Scan plugins"}
          </button>
          <button type="button" disabled={busy} onClick={checkUpdates} className="btn-secondary">
            {busy && toast.message?.includes("indexes") ? "Checking…" : "Check updates"}
          </button>
          {stats.supervisor_configured && (
            <button
              type="button"
              disabled={busy}
              onClick={restart}
              className="btn-secondary border-ember/40 bg-ember/10 text-ember hover:bg-ember/20"
            >
              {busy && toast.message?.startsWith("Restarting") ? "Restarting…" : "Restart + sync"}
            </button>
          )}
        </div>
      </div>

      <p className="text-xs text-bark/50">
        Check updates also refreshes store indexes when they are more than a few minutes old.
      </p>
      {stats.restart_required && (
        <div className="rounded-xl border border-ember/30 bg-ember/10 px-4 py-3 text-sm text-ember">
          Plugin or config changes are waiting. Restart + sync stops the game, runs valheim-bootstrap
          (config → live), then starts the server again.
        </div>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Installed" value={stats.installed_count} />
        <Stat label="Updates" value={stats.updates_count} />
        <Stat label="Disabled" value={stats.disabled_count} />
        <Stat label="Unmanaged" value={stats.unmanaged_count} hint="Manual / unmatched plugins" />
      </div>

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-2xl">Available updates</h2>
            <Link to="/installed" className="text-sm text-sea hover:underline">
              Manage
            </Link>
          </div>
          {stats.pending_updates.length === 0 ? (
            <p className="mt-4 text-sm text-bark/60">No pending updates.</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {stats.pending_updates.map((u) => (
                <li
                  key={u.id}
                  className="flex items-center justify-between gap-3 border-b border-bark/10 pb-3 last:border-0"
                >
                  <div>
                    <p className="font-medium">{u.full_name}</p>
                    <p className="text-xs text-bark/60">
                      {u.current_version || "?"} → {u.target_version} · {u.source} · {u.status}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
          <h2 className="font-display text-2xl">Automatic maintenance</h2>
          <p className="mt-3 text-sm text-bark/70">
            {stats.maintenance_enabled ? "Enabled" : "Disabled"}
          </p>
          <p className="mt-1 text-sm text-bark/70">
            Next update window:{" "}
            {stats.next_maintenance_window
              ? formatLocalDateTime(stats.next_maintenance_window)
              : "—"}
          </p>
          <p className="mt-4 text-xs text-bark/50">
            Last scan:{" "}
            {stats.last_scan_at ? formatLocalDateTime(stats.last_scan_at) : "never"}
          </p>
          <p className="text-xs text-bark/50">
            Last update check:{" "}
            {stats.last_update_check_at
              ? formatLocalDateTime(stats.last_update_check_at)
              : "never"}
          </p>
        </div>
      </section>

      <section className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-2xl">Recent activity</h2>
          <Link to="/history" className="text-sm text-sea hover:underline">
            View all
          </Link>
        </div>
        <ul className="mt-4 space-y-2">
          {stats.recent_activity.slice(0, 8).map((a) => (
            <li key={a.id} className="flex flex-wrap gap-2 text-sm">
              <span className="text-bark/50">{formatLocalDateTime(a.timestamp)}</span>
              <span className="font-medium">{a.action}</span>
              <span className="text-bark/70">{a.package || a.message}</span>
              <span className={a.result === "ok" ? "text-moss" : "text-danger"}>{a.result}</span>
            </li>
          ))}
          {stats.recent_activity.length === 0 && (
            <li className="text-sm text-bark/60">No activity yet.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
