import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, InstalledPackage, Package } from "../api/client";

type LinkForm = {
  id: number;
  source: string;
  full_name: string;
  query: string;
};

export default function InstalledPage() {
  const [packages, setPackages] = useState<InstalledPackage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [linkForm, setLinkForm] = useState<LinkForm | null>(null);
  const [suggestions, setSuggestions] = useState<Package[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importName, setImportName] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    setPackages(await api.get<InstalledPackage[]>("/api/plugins"));
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  useEffect(() => {
    if (!linkForm) {
      setSuggestions([]);
      return;
    }
    const q = linkForm.query.trim();
    if (q.length < 2) {
      setSuggestions([]);
      return;
    }
    const handle = setTimeout(() => {
      const params = new URLSearchParams({ q, limit: "12" });
      if (linkForm.source) params.set("source", linkForm.source);
      api
        .get<Package[]>(`/api/packages?${params}`)
        .then(setSuggestions)
        .catch(() => setSuggestions([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [linkForm?.query, linkForm?.source, linkForm]);

  async function act(id: number, fn: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    setInfo(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  async function rescan() {
    setBusy(-1);
    setError(null);
    setInfo(null);
    try {
      const result = await api.post<{
        scanned: number;
        pruned?: string[];
      }>("/api/scan");
      await load();
      const pruned = result.pruned ?? [];
      setInfo(
        pruned.length
          ? `Scan found ${result.scanned} on disk; removed ${pruned.length} missing: ${pruned.join(", ")}`
          : `Scan found ${result.scanned} plugins on disk.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setBusy(null);
    }
  }

  function openLink(pkg: InstalledPackage) {
    const preferred =
      pkg.source === "thunderstore" || pkg.source === "hexium" ? pkg.source : "thunderstore";
    setLinkForm({
      id: pkg.id,
      source: preferred,
      full_name: pkg.full_name.includes("-") ? pkg.full_name : pkg.full_name,
      query: pkg.name || pkg.full_name,
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Installed mods</h2>
          <p className="text-sm text-bark/70">
            Link local/unmanaged plugins to Thunderstore or Hexium for updates. Rescan auto-matches when
            possible.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setImportOpen(true)}
            className="btn-secondary"
          >
            Import zip / DLL
          </button>
          <button
            type="button"
            onClick={() =>
              act(-1, async () => {
                await api.post("/api/packages/refresh");
                await rescan();
              })
            }
            className="btn-secondary"
          >
            Refresh indexes + scan
          </button>
          <button
            type="button"
            onClick={() => rescan()}
            className="btn-primary"
          >
            Rescan
          </button>
        </div>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {info && <p className="text-sm text-sea">{info}</p>}

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
                  {pkg.live_only && (
                    <span className="ml-1 rounded-full bg-sea/15 px-2 py-0.5 text-xs text-sea">
                      persistent
                    </span>
                  )}
                  {pkg.source === "local" && (
                    <span className="ml-1 rounded-full bg-sea/15 px-2 py-0.5 text-xs text-sea">
                      not linked
                    </span>
                  )}
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
                      className="btn-ghost"
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
                      className="btn-ghost"
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
                        className="btn-ghost"
                      >
                        Configure
                      </Link>
                    )}
                    <button
                      type="button"
                      className="btn-ghost text-sea"
                      onClick={() => openLink(pkg)}
                    >
                      {pkg.source === "thunderstore" || pkg.source === "hexium"
                        ? "Change store"
                        : "Link store"}
                    </button>
                    <button
                      type="button"
                      disabled={busy === pkg.id}
                      className="btn-ghost"
                      title={
                        pkg.live_only
                          ? "Move files back into plugins/ (bootstrap will copy every file)"
                          : "Move files to .persistent/ and leave a symlink in plugins/ (bootstrap copies the link only)"
                      }
                      onClick={() =>
                        act(pkg.id, () =>
                          api.post(
                            `/api/plugins/${pkg.id}/${pkg.live_only ? "normal" : "persistent"}`,
                          ),
                        )
                      }
                    >
                      {pkg.live_only ? "Make normal" : "Make persistent"}
                    </button>
                    {pkg.managed && (
                      <button
                        type="button"
                        disabled={busy === pkg.id}
                        className="btn-danger text-xs px-2 py-1"
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

      {importOpen && (
        <div className="fixed inset-0 z-20 grid place-items-center bg-ink/40 p-4">
          <form
            className="w-full max-w-lg rounded-2xl bg-paper p-6"
            onSubmit={(e) => {
              e.preventDefault();
              if (!importFile) {
                setError("Choose a .zip or .dll file");
                return;
              }
              const form = new FormData();
              form.append("file", importFile);
              if (importName.trim()) form.append("full_name", importName.trim());
              act(-1, () => api.upload("/api/plugins/import", form)).then(() => {
                setImportOpen(false);
                setImportFile(null);
                setImportName("");
              });
            }}
          >
            <h3 className="font-display text-2xl">Import mod</h3>
            <p className="mt-1 text-xs text-bark/60">
              Upload a Thunderstore/Hexium-style .zip, or a single plugin .dll. Optional name overrides
              the folder / package id (Team-Mod).
            </p>
            <label className="mt-4 block text-sm">
              File
              <input
                type="file"
                accept=".zip,.dll,application/zip"
                className="mt-1 block w-full text-sm"
                onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="mt-3 block text-sm">
              Package name override (optional)
              <input
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={importName}
                onChange={(e) => setImportName(e.target.value)}
                placeholder="Author-ModName"
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="px-3 py-2 text-sm"
                onClick={() => {
                  setImportOpen(false);
                  setImportFile(null);
                }}
              >
                Cancel
              </button>
              <button type="submit" className="rounded-md bg-moss px-3 py-2 text-sm text-paper">
                Import
              </button>
            </div>
          </form>
        </div>
      )}

      {linkForm && (
        <div className="fixed inset-0 z-20 grid place-items-center bg-ink/40 p-4">
          <form
            className="w-full max-w-lg rounded-2xl bg-paper p-6"
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
            <h3 className="font-display text-2xl">Link to store package</h3>
            <p className="mt-1 text-xs text-bark/60">
              Pick Thunderstore or Hexium. Search and click a result, or type Team-Mod exactly.
            </p>
            <label className="mt-4 block text-sm">
              Store
              <select
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={linkForm.source}
                onChange={(e) => setLinkForm({ ...linkForm, source: e.target.value })}
              >
                <option value="thunderstore">Thunderstore</option>
                <option value="hexium">Hexium</option>
              </select>
            </label>
            <label className="mt-3 block text-sm">
              Search
              <input
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={linkForm.query}
                onChange={(e) => setLinkForm({ ...linkForm, query: e.target.value })}
                placeholder="Jotunn, PortalAtlas…"
              />
            </label>
            {suggestions.length > 0 && (
              <ul className="mt-2 max-h-48 overflow-auto rounded border border-bark/15 text-sm">
                {suggestions.map((s) => (
                  <li key={`${s.source}:${s.full_name}`}>
                    <button
                      type="button"
                      className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-mist"
                      onClick={() =>
                        setLinkForm({
                          ...linkForm,
                          source: s.source,
                          full_name: s.full_name,
                          query: s.full_name,
                        })
                      }
                    >
                      <span className="rounded bg-mist px-1.5 text-[10px] uppercase">{s.source}</span>
                      <span>
                        <span className="font-medium">{s.full_name}</span>
                        <span className="block text-xs text-bark/60">
                          {s.latest_version} · {s.downloads.toLocaleString()} downloads
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label className="mt-3 block text-sm">
              Full name (Team-Mod)
              <input
                className="mt-1 w-full rounded border border-bark/20 px-3 py-2"
                value={linkForm.full_name}
                onChange={(e) => setLinkForm({ ...linkForm, full_name: e.target.value })}
                required
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="px-3 py-2 text-sm" onClick={() => setLinkForm(null)}>
                Cancel
              </button>
              <button type="submit" className="rounded-md bg-moss px-3 py-2 text-sm text-paper">
                Save link
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
