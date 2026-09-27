import { useEffect, useState } from "react";
import { api, Backup } from "../api/client";

export default function BackupsPage() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBackups(await api.get<Backup[]>("/api/backups"));
  }

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);

  async function createBackup() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/backups", { label: `manual-${new Date().toISOString()}` });
      setMessage("Backup created.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backup failed");
    } finally {
      setBusy(false);
    }
  }

  async function restore(id: number, label: string) {
    if (!confirm(`Restore backup "${label}"? Files changed after the backup that differ will be skipped.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/backups/${id}/restore`);
      setMessage("Backup restored. Restart may be required.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restore failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Backups</h2>
          <p className="text-sm text-bark/70">
            Mod-state backups only. World saves stay with the Valheim server.
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={createBackup}
          className="rounded-md bg-moss px-3 py-2 text-sm text-paper"
        >
          Create backup
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {message && <p className="text-sm text-moss-deep">{message}</p>}
      <div className="overflow-x-auto rounded-2xl border border-bark/10 bg-paper/80">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-bark/10 text-xs uppercase text-bark/60">
            <tr>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Label</th>
              <th className="px-4 py-3">Reason</th>
              <th className="px-4 py-3">Packages</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {backups.map((b) => (
              <tr key={b.id} className="border-b border-bark/5">
                <td className="px-4 py-2 whitespace-nowrap">
                  {new Date(b.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-2">{b.label}</td>
                <td className="px-4 py-2">{b.reason}</td>
                <td className="px-4 py-2 text-bark/70">
                  {b.package_names.slice(0, 4).join(", ")}
                  {b.package_names.length > 4 ? "…" : ""}
                </td>
                <td className="px-4 py-2">
                  <button
                    type="button"
                    disabled={busy}
                    className="rounded border border-bark/20 px-2 py-1 text-xs"
                    onClick={() => restore(b.id, b.label)}
                  >
                    Restore
                  </button>
                </td>
              </tr>
            ))}
            {backups.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-bark/60" colSpan={5}>
                  No backups yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
