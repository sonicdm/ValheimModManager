import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, InstalledPackage } from "../api/client";

export default function InstalledPage() {
  const [packages, setPackages] = useState<InstalledPackage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [linkForm, setLinkForm] = useState<{ id: number; source: string; full_name: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    setPackages(await api.get<InstalledPackage[]>("/api/plugins"));
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  async function act(id: number, fn: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Installed mods</h2>
          <p className="text-sm text-bark/70">Managed Thunderstore/Hexium packages and unmanaged plugins.</p>
        </div>
        <button
          type="button"
          onClick={() => act(-1, () => api.post("/api/scan")).then(load)}
          className="rounded-md bg-moss px-3 py-2 text-sm text-paper"
        >
          Rescan
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="overflow-x-auto rounded-2xl border border-bark/10 bg-paper/80">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-bark/10 text-xs uppercase tracking-wide text-bark/60">
            <tr>
              <th className="px-4 py-3">Mod</th>
              <th className="px-4 py-3">Version</th>
              <th className="px-4 py-3">Source</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {packages.map((pkg) => (
              <tr key={pkg.id} className="border-b border-bark/5 align-top">
                <td className="px-4 py-3">
                  <p className="font-medium">{pkg.name}</p>
                  <p className="text-xs text-bark/50">{pkg.full_name}</p>
                  {pkg.update_available && (
                    <p className="text-xs text-ember">Update → {pkg.update_available}</p>
                  )}
                </td>
                <td className="px-4 py-3">{pkg.version || "—"}</td>
                <td className="px-4 py-3">
                  <span className="rounded-full bg-mist px-2 py-0.5 text-xs">{pkg.source}</span>
                  {!pkg.managed && (
                    <span className="ml-1 rounded-full bg-ember/15 px-2 py-0.5 text-xs text-ember">
                      unmanaged
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {pkg.enabled ? "Enabled" : "Disabled"}
                  {pkg.pinned && " · Pinned"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    <button
                      type="button"
                      disabled={busy === pkg.id}
                      className="rounded border border-bark/15 px-2 py-1 text-xs"
                      onClick={() =>
                        act(pkg.id, () =>
                          api.post(`/api/plugins/${pkg.id}/${pkg.enabled ? "disable" : "enable"}`),
                        )
                      }
                    >
                      {pkg.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      disabled={busy === pkg.id}
                      className="rounded border border-bark/15 px-2 py-1 text-xs"
                      onClick={() =>
                        act(pkg.id, () =>
                          api.patch(`/api/plugins/${pkg.id}`, { pinned: !pkg.pinned }),
                        )
                      }
                    >
                      {pkg.pinned ? "Unpin" : "Pin"}
                    </button>
                    {pkg.config_files[0] && (
                      <Link
                        to={`/config?file=${encodeURIComponent(pkg.config_files[0])}`}
                        className="rounded border border-bark/15 px-2 py-1 text-xs"
                      >
                        Configure
                      </Link>
                    )}
                    {!pkg.managed && (
                      <button
                        type="button"
                        className="rounded border border-sea/30 px-2 py-1 text-xs text-sea"
                        onClick={() =>
                          setLinkForm({ id: pkg.id, source: "hexium", full_name: pkg.full_name })
                        }
                      >
                        Link
                      </button>
                    )}
                    {pkg.managed && (
                      <button
                        type="button"
                        disabled={busy === pkg.id}
                        className="rounded border border-danger/30 px-2 py-1 text-xs text-danger"
                        onClick={() => {
                          if (confirm(`Uninstall ${pkg.full_name}?`)) {
                            act(pkg.id, () => api.delete(`/api/plugins/${pkg.id}`));
                          }
                        }}
                      >
                        Uninstall
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {linkForm && (
        <div className="fixed inset-0 z-20 grid place-items-center bg-ink/40 p-4">
          <form
            className="w-full max-w-md rounded-2xl bg-paper p-6"
            onSubmit={(e) => {
              e.preventDefault();
              act(linkForm.id, () =>
                api.post(`/api/plugins/${linkForm.id}/link`, {
                  source: linkForm.source,
                  full_name: linkForm.full_name,
                }),
              ).then(() => setLinkForm(null));
            }}
          >
            <h3 className="font-display text-2xl">Link to package</h3>
            <label className="mt-4 block text-sm">
              Source
              <select
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={linkForm.source}
                onChange={(e) => setLinkForm({ ...linkForm, source: e.target.value })}
              >
                <option value="hexium">Hexium</option>
                <option value="thunderstore">Thunderstore</option>
              </select>
            </label>
            <label className="mt-3 block text-sm">
              Full name (Team-Mod)
              <input
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={linkForm.full_name}
                onChange={(e) => setLinkForm({ ...linkForm, full_name: e.target.value })}
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="px-3 py-2 text-sm" onClick={() => setLinkForm(null)}>
                Cancel
              </button>
              <button type="submit" className="rounded-md bg-moss px-3 py-2 text-sm text-paper">
                Link
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
