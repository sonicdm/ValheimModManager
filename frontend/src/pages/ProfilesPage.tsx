import { FormEvent, useEffect, useRef, useState } from "react";
import {
  api,
  getCsrfToken,
  Profile,
  ProfileActivateResult,
  ProfileCodeOut,
} from "../api/client";
import { StatusToast, useStatusToast } from "../components/StatusToast";

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("ServerDefault");
  const [importCode, setImportCode] = useState("");
  const [lastCode, setLastCode] = useState<string | null>(null);
  const [lastActivate, setLastActivate] = useState<ProfileActivateResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useStatusToast();

  async function load() {
    setProfiles(await api.get<Profile[]>("/api/profiles"));
  }

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);

  async function saveCurrent() {
    setBusy(true);
    setError(null);
    toast.showBusy("Saving current mods as profile…");
    try {
      await api.post("/api/profiles", {
        name: newName.trim() || "ServerDefault",
        include_configs: true,
        from_current: true,
      });
      toast.showOk("Profile saved.");
      setMessage("Profile saved from current install.");
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function activate(id: number, name: string) {
    if (
      !confirm(
        `Apply "${name}" to the server? This replaces managed mods (persistent packs are kept). A backup is created first.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    toast.showBusy(`Applying profile "${name}"…`);
    try {
      const result = await api.post<ProfileActivateResult>(`/api/profiles/${id}/activate`, {});
      setLastActivate(result);
      const errBit = result.errors.length ? ` (${result.errors.length} errors)` : "";
      toast.showOk(`Profile applied${errBit}. Restart may be required.`);
      setMessage(`Applied "${name}" to the server. Restart may be required.`);
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not apply profile";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function exportFile(id: number, mode: "full" | "thunderstore") {
    setBusy(true);
    setError(null);
    try {
      const headers = new Headers();
      const csrf = getCsrfToken();
      if (csrf) headers.set("X-CSRF-Token", csrf);
      const res = await fetch(`/api/profiles/${id}/export?mode=${mode}`, {
        credentials: "include",
        headers,
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const match = /filename="?([^"]+)"?/.exec(cd);
      const filename = match?.[1] || `profile-${mode}.r2z`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.showOk("Profile file downloaded.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Download failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function exportCode(id: number) {
    setBusy(true);
    setError(null);
    toast.showBusy("Creating share code…");
    try {
      const out = await api.post<ProfileCodeOut>(`/api/profiles/${id}/export-code?mode=full`);
      setLastCode(out.code);
      await navigator.clipboard.writeText(out.code);
      toast.showOk("Share code copied to clipboard.");
      setMessage(`Share code ready: ${out.code}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not create share code";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number, name: string) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    setBusy(true);
    try {
      await api.delete(`/api/profiles/${id}`);
      await load();
      toast.showOk("Profile deleted.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function importFromCode(e: FormEvent) {
    e.preventDefault();
    if (!importCode.trim()) return;
    setBusy(true);
    setError(null);
    toast.showBusy("Importing profile code…");
    try {
      await api.post("/api/profiles/import-code", {
        code: importCode.trim(),
        include_configs: true,
        activate: false,
      });
      setImportCode("");
      toast.showOk("Profile imported. Apply it to the server when ready.");
      setMessage("Imported. Review the list, then Apply to server.");
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Import failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function importFile(file: File) {
    setBusy(true);
    setError(null);
    toast.showBusy(`Importing ${file.name}…`);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("include_configs", "true");
      form.append("activate", "false");
      await api.upload("/api/profiles/import", form);
      toast.showOk("Profile imported. Apply it to the server when ready.");
      setMessage("Imported from file. Review the list, then Apply to server.");
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Import failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <StatusToast message={toast.message} tone={toast.tone} busy={toast.busy} />
      <div>
        <h2 className="font-display text-3xl">Profiles</h2>
        <p className="text-sm text-bark/70">
          Save named mod lists for this server, then apply one when you want it live. Import/export
          uses the same <code className="text-xs">.r2z</code> files and share codes as Gale — fully
          interoperable both ways, including Hexium. r2modman / Thunderstore Mod Manager only work
          if every mod and version is on Thunderstore; otherwise use{" "}
          <strong>Download Thunderstore-only</strong> (remaps or drops what Thunderstore can’t
          resolve).
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      {message && <p className="text-sm text-moss-deep">{message}</p>}
      {lastCode && (
        <p className="rounded-lg border border-bark/10 bg-paper/80 p-3 font-mono text-sm break-all">
          Last share code: {lastCode}
        </p>
      )}
      {lastActivate && (
        <div className="rounded-lg border border-bark/10 bg-paper/80 p-3 text-sm text-bark">
          <p className="font-semibold text-ink">Last applied</p>
          <p>
            +{lastActivate.installed.length} / ~{lastActivate.updated.length} / −
            {lastActivate.removed.length}
            {lastActivate.kept_persistent.length
              ? ` · kept persistent: ${lastActivate.kept_persistent.join(", ")}`
              : ""}
          </p>
          {lastActivate.errors.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-danger">
              {lastActivate.errors.map((err) => (
                <li key={err}>{err}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-bark/10 bg-paper/80 p-4 space-y-3">
          <h3 className="font-display text-xl">Save current mods</h3>
          <input
            className="w-full rounded-md border border-bark/20 bg-paper px-3 py-2 text-sm"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Profile name"
          />
          <button
            type="button"
            disabled={busy}
            onClick={saveCurrent}
            className="rounded-md bg-moss px-3 py-2 text-sm text-on-moss"
          >
            Save as new profile
          </button>
        </div>

        <div className="rounded-2xl border border-bark/10 bg-paper/80 p-4 space-y-3">
          <h3 className="font-display text-xl">Import a profile</h3>
          <form onSubmit={importFromCode} className="flex flex-col gap-2">
            <input
              className="w-full rounded-md border border-bark/20 bg-paper px-3 py-2 font-mono text-sm"
              value={importCode}
              onChange={(e) => setImportCode(e.target.value)}
              placeholder="Paste share code"
            />
            <button
              type="submit"
              disabled={busy || !importCode.trim()}
              className="rounded-md bg-moss px-3 py-2 text-sm text-on-moss"
            >
              Import from share code
            </button>
          </form>
          <div>
            <input
              ref={fileRef}
              type="file"
              accept=".r2z,.zip"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importFile(f);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
              className="btn-secondary px-3 py-2 text-sm"
            >
              Import from file
            </button>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="font-display text-xl">Saved profiles</h3>
        {profiles.length === 0 && <p className="text-sm text-bark/60">No profiles yet.</p>}
        <ul className="space-y-3">
          {profiles.map((p) => (
            <li
              key={p.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-bark/10 bg-paper/80 p-4"
            >
              <div>
                <p className="font-semibold text-ink">
                  {p.name}
                  {p.is_active ? (
                    <span className="ml-2 text-xs font-normal uppercase tracking-wide text-moss-deep">
                      Active
                    </span>
                  ) : null}
                </p>
                <p className="text-xs text-bark/60">
                  {p.mod_count} mods
                  {p.include_configs ? " · includes configs" : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => activate(p.id, p.name)}
                  className="rounded-md bg-moss px-3 py-1.5 text-sm text-on-moss"
                >
                  Apply to server
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => exportFile(p.id, "full")}
                  className="rounded-md bg-moss px-3 py-1.5 text-sm text-on-moss"
                  title="Download .r2z (full profile — works with Gale)"
                >
                  Download profile file
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => exportCode(p.id)}
                  className="btn-secondary px-3 py-1.5 text-sm"
                  title="Share code (works with Gale; r2modman only if everything is on Thunderstore)"
                >
                  Copy share code
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => exportFile(p.id, "thunderstore")}
                  className="btn-secondary px-3 py-1.5 text-sm"
                  title="For r2modman / Thunderstore Mod Manager — remaps or drops Hexium-only mods/versions"
                >
                  Download Thunderstore-only
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => remove(p.id, p.name)}
                  className="px-3 py-1.5 text-sm text-danger"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
