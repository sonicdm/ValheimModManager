import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, InstalledPackage, Package } from "../api/client";
import RowMenu from "../components/RowMenu";
import { StatusToast, useStatusToast } from "../components/StatusToast";
import { summarizeApply, type ApplyResult } from "../lib/updates";

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
  const toast = useStatusToast();

  const load = useCallback(async () => {
    setPackages(await api.get<InstalledPackage[]>("/api/plugins"));
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  useEffect(() => {
    function onScanDone() {
      load().catch(() => undefined);
    }
    window.addEventListener("vmm:scan-done", onScanDone);
    return () => window.removeEventListener("vmm:scan-done", onScanDone);
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

  async function act(
    id: number,
    fn: () => Promise<unknown>,
    labels?: { busy: string; ok: string },
  ) {
    setBusy(id);
    setError(null);
    setInfo(null);
    toast.showBusy(labels?.busy ?? "Working…");
    try {
      await fn();
      await load();
      toast.showOk(labels?.ok ?? "Done.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Action failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(null);
    }
  }

  async function rescan() {
    setBusy(-1);
    setError(null);
    setInfo(null);
    toast.showBusy("Scanning plugins on disk…");
    try {
      const result = await api.post<{
        scanned: number;
        pruned?: string[];
      }>("/api/scan");
      await load();
      const pruned = result.pruned ?? [];
      const msg = pruned.length
        ? `Scan found ${result.scanned} on disk; removed ${pruned.length} missing: ${pruned.join(", ")}`
        : `Scan found ${result.scanned} plugins on disk.`;
      setInfo(msg);
      toast.showOk(msg);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Scan failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(null);
    }
  }

  async function refreshIndexesAndScan() {
    setBusy(-1);
    setError(null);
    setInfo(null);
    toast.showBusy("Updating store indexes…");
    try {
      await api.post("/api/packages/refresh");
      toast.showBusy("Indexes updated — scanning plugins…");
      const result = await api.post<{
        scanned: number;
        pruned?: string[];
      }>("/api/scan");
      await load();
      const pruned = result.pruned ?? [];
      const msg = pruned.length
        ? `Indexes refreshed. Scan found ${result.scanned}; pruned ${pruned.length}.`
        : `Indexes refreshed. Scan found ${result.scanned} plugins.`;
      setInfo(msg);
      toast.showOk(msg);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Refresh failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(null);
    }
  }

  async function applyUpdates(fullNames?: string[]) {
    const label =
      fullNames?.length === 1
        ? `Updating ${fullNames[0]}…`
        : fullNames?.length
          ? `Updating ${fullNames.length} packages…`
          : "Updating all pending packages…";
    setBusy(-2);
    setError(null);
    setInfo(null);
    toast.showBusy(label);
    try {
      const data = await api.post<ApplyResult>(
        "/api/updates/apply",
        fullNames?.length ? { full_names: fullNames } : {},
      );
      await load();
      const msg = summarizeApply(data);
      const anyFail = data.results.some((r) => !r.ok);
      if (anyFail) {
        setError(msg);
        setInfo(null);
        toast.showError(msg);
      } else {
        setInfo(msg);
        toast.showOk(msg);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Update failed";
      setError(msg);
      toast.showError(msg);
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

  const pendingCount = packages.filter((p) => p.update_available).length;
  const updating = busy === -2;

  return (
    <div className="space-y-4">
      <StatusToast message={toast.message} tone={toast.tone} busy={toast.busy} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Installed mods</h2>
          <p className="text-sm text-bark/70">
            Link local/unmanaged plugins to Thunderstore or Hexium for updates. Rescan auto-matches when
            possible.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setImportOpen(true)}
            className="btn-secondary"
          >
            Import zip / DLL
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => refreshIndexesAndScan()}
            className="btn-secondary"
          >
            {busy === -1 && toast.message?.includes("indexes")
              ? "Refreshing…"
              : "Refresh indexes + scan"}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => rescan()}
            className="btn-primary"
          >
            {busy === -1 && toast.message?.includes("Scanning") ? "Scanning…" : "Rescan"}
          </button>
        </div>
      </div>
      {error && <p className="whitespace-pre-line text-sm text-danger">{error}</p>}
      {info && <p className="whitespace-pre-line text-sm text-sea">{info}</p>}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-bark/60">
          {pendingCount
            ? `${pendingCount} update${pendingCount === 1 ? "" : "s"} available`
            : "All installed mods are up to date"}
        </p>
        <button
          type="button"
          disabled={busy !== null || pendingCount === 0}
          className="btn-secondary border-ember/40 bg-ember/10 text-ember hover:bg-ember/20"
          onClick={() => applyUpdates()}
        >
          {updating ? "Updating…" : "Update all"}
        </button>
      </div>

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
                  <div className="flex flex-wrap items-center gap-1">
                    {pkg.update_available && (
                      <button
                        type="button"
                        disabled={busy !== null}
                        className="btn-ghost border-ember/30 text-ember"
                        onClick={() => applyUpdates([pkg.full_name])}
                      >
                        Update
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busy === pkg.id || updating}
                      className="btn-ghost"
                      onClick={() =>
                        act(
                          pkg.id,
                          () =>
                            api.post(`/api/plugins/${pkg.id}/${pkg.enabled ? "disable" : "enable"}`),
                          {
                            busy: pkg.enabled ? `Disabling ${pkg.name}…` : `Enabling ${pkg.name}…`,
                            ok: pkg.enabled ? `${pkg.name} disabled.` : `${pkg.name} enabled.`,
                          },
                        )
                      }
                    >
                      {pkg.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      disabled={busy === pkg.id || updating}
                      className="btn-ghost"
                      onClick={() =>
                        act(
                          pkg.id,
                          () => api.patch(`/api/plugins/${pkg.id}`, { pinned: !pkg.pinned }),
                          {
                            busy: pkg.pinned ? `Unpinning ${pkg.name}…` : `Pinning ${pkg.name}…`,
                            ok: pkg.pinned ? `${pkg.name} unpinned.` : `${pkg.name} pinned.`,
                          },
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
                    <RowMenu
                      disabled={busy === pkg.id || updating}
                      items={[
                        {
                          key: "link",
                          label:
                            pkg.source === "thunderstore" || pkg.source === "hexium"
                              ? "Change store…"
                              : "Link store…",
                          onClick: () => openLink(pkg),
                        },
                        {
                          key: "persist",
                          label: pkg.live_only ? "Make normal" : "Make persistent",
                          title: pkg.live_only
                            ? "Move files back into plugins/ (bootstrap will copy every file)"
                            : "Move files to .persistent/ and leave a symlink in plugins/",
                          onClick: () =>
                            act(
                              pkg.id,
                              () =>
                                api.post(
                                  `/api/plugins/${pkg.id}/${pkg.live_only ? "normal" : "persistent"}`,
                                ),
                              {
                                busy: pkg.live_only
                                  ? `Moving ${pkg.name} back to plugins…`
                                  : `Making ${pkg.name} persistent…`,
                                ok: pkg.live_only
                                  ? `${pkg.name} is a normal plugins install.`
                                  : `${pkg.name} marked persistent.`,
                              },
                            ),
                        },
                        ...(pkg.managed
                          ? [
                              {
                                key: "uninstall",
                                label: "Uninstall",
                                danger: true,
                                onClick: () => {
                                  if (confirm(`Uninstall ${pkg.full_name}?`)) {
                                    act(
                                      pkg.id,
                                      () => api.delete(`/api/plugins/${pkg.id}`),
                                      {
                                        busy: `Uninstalling ${pkg.name}…`,
                                        ok: `${pkg.name} uninstalled.`,
                                      },
                                    );
                                  }
                                },
                              },
                            ]
                          : []),
                      ]}
                    />
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
              act(-1, () => api.upload("/api/plugins/import", form), {
                busy: `Importing ${importFile.name}…`,
                ok: `Imported ${importFile.name}.`,
              }).then(() => {
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
              <button type="submit" className="rounded-md bg-moss px-3 py-2 text-sm text-on-moss">
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
              act(
                linkForm.id,
                () =>
                  api.post(`/api/plugins/${linkForm.id}/link`, {
                    source: linkForm.source,
                    full_name: linkForm.full_name,
                  }),
                {
                  busy: `Linking to ${linkForm.full_name}…`,
                  ok: `Linked to ${linkForm.full_name}.`,
                },
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
              <button type="submit" className="rounded-md bg-moss px-3 py-2 text-sm text-on-moss">
                Save link
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
