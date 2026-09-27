import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Package } from "../api/client";

type InstallPreviewResponse = {
  packages: { source: string; full_name: string; version: string; dependencies: string[] }[];
  conflicts: string[];
  warnings: string[];
};

export default function PackageDetailPage() {
  const { source = "", fullName = "" } = useParams();
  const decoded = decodeURIComponent(fullName);
  const [pkg, setPkg] = useState<Package | null>(null);
  const [version, setVersion] = useState<string>("");
  const [preview, setPreview] = useState<InstallPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Package>(`/api/packages/${source}/${encodeURIComponent(decoded)}`)
      .then((p) => {
        setPkg(p);
        setVersion(p.latest_version || "");
      })
      .catch((e) => setError(e.message));
  }, [source, decoded]);

  async function doPreview() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<InstallPreviewResponse>("/api/packages/preview", {
        source,
        full_name: decoded,
        version: version || null,
      });
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function doInstall() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.post("/api/packages/install", {
        source,
        full_name: decoded,
        version: version || null,
      });
      setMessage("Installed. A server restart may be required.");
      const refreshed = await api.get<Package>(
        `/api/packages/${source}/${encodeURIComponent(decoded)}`,
      );
      setPkg(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Install failed");
    } finally {
      setBusy(false);
    }
  }

  if (!pkg && !error) return <p className="text-bark/70">Loading package…</p>;
  if (!pkg) return <p className="text-danger">{error}</p>;

  return (
    <div className="space-y-6">
      <Link to="/discover" className="text-sm text-sea hover:underline">
        ← Discover
      </Link>
      <div className="flex flex-wrap gap-4">
        {pkg.icon_url && (
          <img src={pkg.icon_url} alt="" className="h-24 w-24 rounded-xl object-cover" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-display text-4xl">{pkg.name}</h2>
            <span className="rounded-full bg-mist px-2 py-0.5 text-xs uppercase">{pkg.source}</span>
          </div>
          <p className="text-sm text-bark/70">
            {pkg.owner} · {pkg.full_name}
          </p>
          {pkg.package_url && (
            <a
              href={pkg.package_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-sea hover:underline"
            >
              Open on {pkg.source}
            </a>
          )}
          <p className="mt-3 max-w-2xl text-sm text-bark/80">{pkg.description}</p>
        </div>
      </div>

      <div className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
        <h3 className="font-display text-2xl">Install</h3>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-sm">
            Version
            <select
              className="mt-1 block rounded-md border border-bark/20 bg-white px-3 py-2"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            >
              {(pkg.versions || []).map((v) => (
                <option key={v.version_number} value={v.version_number}>
                  {v.version_number}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={doPreview}
            className="rounded-md border border-bark/20 px-3 py-2 text-sm"
          >
            Preview
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={doInstall}
            className="rounded-md bg-moss px-3 py-2 text-sm text-paper"
          >
            {pkg.installed ? "Reinstall / Update" : "Install"}
          </button>
        </div>
        {pkg.installed && (
          <p className="mt-2 text-xs text-bark/60">Installed version: {pkg.installed_version}</p>
        )}
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        {message && <p className="mt-2 text-sm text-moss-deep">{message}</p>}
        {preview && (
          <div className="mt-4 space-y-2 text-sm">
            <p className="font-medium">Install plan</p>
            <ul className="list-disc pl-5">
              {preview.packages.map((p) => (
                <li key={`${p.source}:${p.full_name}`}>
                  {p.full_name} {p.version} ({p.source})
                </li>
              ))}
            </ul>
            {preview.warnings.map((w) => (
              <p key={w} className="text-ember">
                {w}
              </p>
            ))}
            {preview.conflicts.map((c) => (
              <p key={c} className="text-danger">
                {c}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
        <h3 className="font-display text-2xl">Dependencies</h3>
        <ul className="mt-3 list-disc pl-5 text-sm text-bark/80">
          {(pkg.dependencies || []).map((d) => (
            <li key={d}>{d}</li>
          ))}
          {(pkg.dependencies || []).length === 0 && <li>None declared</li>}
        </ul>
      </div>
    </div>
  );
}
