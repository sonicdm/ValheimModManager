import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";

export default function SettingsPage() {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });

  useEffect(() => {
    api
      .get<{ values: Record<string, unknown> }>("/api/settings")
      .then((r) => setValues(r.values))
      .catch((e) => setError(e.message));
  }, []);

  function setField(key: string, value: unknown) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    try {
      const res = await api.put<{ values: Record<string, unknown> }>("/api/settings", { values });
      setValues(res.values);
      setMessage("Settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/api/auth/password", {
        current_password: pw.current,
        new_password: pw.next,
      });
      setPw({ current: "", next: "" });
      setMessage("Password updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password change failed");
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl">Settings</h2>
        <p className="text-sm text-bark/70">Persisted in the manager data volume.</p>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {message && <p className="text-sm text-moss-deep">{message}</p>}

      <form onSubmit={save} className="space-y-6 rounded-2xl border border-bark/10 bg-paper/80 p-5">
        <fieldset className="grid gap-3 md:grid-cols-2">
          <legend className="font-display text-xl">Server</legend>
          <label className="text-sm">
            Display name
            <input
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.container_display_name ?? "")}
              onChange={(e) => setField("container_display_name", e.target.value)}
            />
          </label>
          <label className="text-sm">
            Time zone
            <input
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.timezone ?? "")}
              onChange={(e) => setField("timezone", e.target.value)}
            />
          </label>
        </fieldset>

        <fieldset className="grid gap-3 md:grid-cols-2">
          <legend className="font-display text-xl">Supervisor</legend>
          <label className="text-sm md:col-span-2">
            Supervisor URL
            <input
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.supervisor_url ?? "")}
              onChange={(e) => setField("supervisor_url", e.target.value)}
              placeholder="http://valheim:9001"
            />
          </label>
          <label className="text-sm">
            User
            <input
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.supervisor_user ?? "")}
              onChange={(e) => setField("supervisor_user", e.target.value)}
            />
          </label>
          <label className="text-sm">
            Password
            <input
              type="password"
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.supervisor_password ?? "")}
              onChange={(e) => setField("supervisor_password", e.target.value)}
              placeholder="leave *** unchanged"
            />
          </label>
          <label className="text-sm">
            Program name
            <input
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.supervisor_program ?? "")}
              onChange={(e) => setField("supervisor_program", e.target.value)}
            />
          </label>
        </fieldset>

        <fieldset className="grid gap-3 md:grid-cols-2">
          <legend className="font-display text-xl">Package sources</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.thunderstore_enabled)}
              onChange={(e) => setField("thunderstore_enabled", e.target.checked)}
            />
            Thunderstore
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.hexium_enabled)}
              onChange={(e) => setField("hexium_enabled", e.target.checked)}
            />
            Hexium
          </label>
        </fieldset>

        <fieldset className="grid gap-3 md:grid-cols-2">
          <legend className="font-display text-xl">Updates</legend>
          <label className="text-sm">
            Policy
            <select
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={String(values.update_policy ?? "all")}
              onChange={(e) => setField("update_policy", e.target.value)}
            >
              <option value="all">Automatic for all managed</option>
              <option value="selected">Selected only (auto_update flag)</option>
              <option value="notify">Notification only</option>
              <option value="manual">Manual</option>
            </select>
          </label>
          <label className="text-sm">
            Check interval (minutes)
            <input
              type="number"
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={Number(values.update_check_minutes ?? 60)}
              onChange={(e) => setField("update_check_minutes", Number(e.target.value))}
            />
          </label>
          <label className="text-sm">
            Maintenance hour
            <input
              type="number"
              min={0}
              max={23}
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={Number(values.maintenance_hour ?? 4)}
              onChange={(e) => setField("maintenance_hour", Number(e.target.value))}
            />
          </label>
          <label className="text-sm">
            Maintenance minute
            <input
              type="number"
              min={0}
              max={59}
              className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
              value={Number(values.maintenance_minute ?? 0)}
              onChange={(e) => setField("maintenance_minute", Number(e.target.value))}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.maintenance_enabled)}
              onChange={(e) => setField("maintenance_enabled", e.target.checked)}
            />
            Maintenance enabled
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.maintenance_lock)}
              onChange={(e) => setField("maintenance_lock", e.target.checked)}
            />
            Global maintenance lock
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.backup_before_update)}
              onChange={(e) => setField("backup_before_update", e.target.checked)}
            />
            Backup before update
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(values.restart_after_updates)}
              onChange={(e) => setField("restart_after_updates", e.target.checked)}
            />
            Restart after updates
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input
              type="checkbox"
              checked={Boolean(values.force_restart_when_players_unknown)}
              onChange={(e) => setField("force_restart_when_players_unknown", e.target.checked)}
            />
            Force restart when player status is unknown (off by default — fail safe)
          </label>
        </fieldset>

        <button type="submit" className="rounded-md bg-moss px-4 py-2 text-paper">
          Save settings
        </button>
      </form>

      <form onSubmit={changePassword} className="space-y-3 rounded-2xl border border-bark/10 bg-paper/80 p-5">
        <h3 className="font-display text-xl">Change admin password</h3>
        <label className="block text-sm">
          Current password
          <input
            type="password"
            className="mt-1 w-full max-w-md rounded border border-bark/20 px-3 py-2"
            value={pw.current}
            onChange={(e) => setPw({ ...pw, current: e.target.value })}
          />
        </label>
        <label className="block text-sm">
          New password
          <input
            type="password"
            className="mt-1 w-full max-w-md rounded border border-bark/20 px-3 py-2"
            value={pw.next}
            onChange={(e) => setPw({ ...pw, next: e.target.value })}
          />
        </label>
        <button type="submit" className="rounded-md border border-bark/20 px-4 py-2 text-sm">
          Update password
        </button>
      </form>
    </div>
  );
}
