import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, InstalledPackage, Package } from "../api/client";

type InstallPreviewResponse = {
  packages: { source: string; full_name: string; version: string; dependencies: string[] }[];
  conflicts: string[];
  warnings: string[];
};

type BusyAction = "preview" | "install" | null;

const INSTALL_STATUS_STEPS = [
  "Downloading package…",
  "Resolving dependencies…",
  "Extracting and copying files…",
  "Registering package…",
];

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
}

export default function PackageDetailPage() {
  const { source = "", fullName = "" } = useParams();
  const decoded = decodeURIComponent(fullName);
  const [pkg, setPkg] = useState<Package | null>(null);
  const [version, setVersion] = useState<string>("");
  const [preview, setPreview] = useState<InstallPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [installed, setInstalled] = useState<InstalledPackage[] | null>(null);
  const stepTimer = useRef<number | null>(null);
  const elapsedTimer = useRef<number | null>(null);

  useEffect(() => {
    api
      .get<Package>(`/api/packages/${source}/${encodeURIComponent(decoded)}`)
      .then((p) => {
        setPkg(p);
        setVersion(p.latest_version || "");
      })
      .catch((e) => setError(e.message));
  }, [source, decoded]);

  useEffect(() => {
    return () => {
      if (stepTimer.current) window.clearInterval(stepTimer.current);
      if (elapsedTimer.current) window.clearInterval(elapsedTimer.current);
    };
  }, []);

  function clearProgressTimers() {
    if (stepTimer.current) {
      window.clearInterval(stepTimer.current);
      stepTimer.current = null;
    }
    if (elapsedTimer.current) {
      window.clearInterval(elapsedTimer.current);
      elapsedTimer.current = null;
    }
  }

  function startInstallProgress() {
    clearProgressTimers();
    setElapsed(0);
    setStatusLine(INSTALL_STATUS_STEPS[0]);
    let step = 0;
    stepTimer.current = window.setInterval(() => {
      step = Math.min(step + 1, INSTALL_STATUS_STEPS.length - 1);
      setStatusLine(INSTALL_STATUS_STEPS[step]);
    }, 4000);
    elapsedTimer.current = window.setInterval(() => {
      setElapsed((n) => n + 1);
    }, 1000);
  }

  async function doPreview() {
    setBusy("preview");
    setError(null);
    setInstalled(null);
    setStatusLine("Building install plan…");
    try {
      const result = await api.post<InstallPreviewResponse>("/api/packages/preview", {
        source,
        full_name: decoded,
        version: version || null,
      });
      setPreview(result);
      setStatusLine(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
      setStatusLine(null);
    } finally {
      setBusy(null);
    }
  }

  async function doInstall() {
    setBusy("install");
    setError(null);
    setInstalled(null);
    startInstallProgress();
    try {
      const packages = await api.post<InstalledPackage[]>("/api/packages/install", {
        source,
        full_name: decoded,
        version: version || null,
      });
      clearProgressTimers();
      setInstalled(packages);
      setStatusLine(null);
      setPreview(null);
      const refreshed = await api.get<Package>(
        `/api/packages/${source}/${encodeURIComponent(decoded)}`,
      );
      setPkg(refreshed);
    } catch (e) {
      clearProgressTimers();
      setError(e instanceof Error ? e.message : "Install failed");
      setStatusLine(null);
    } finally {
      setBusy(null);
    }
  }

  if (!pkg && !error) return <p className="text-bark/70">Loading package…</p>;
  if (!pkg) return <p className="text-danger">{error}</p>;

  const installing = busy === "install";
  const previewing = busy === "preview";
  const installLabel = installing
    ? "Installing…"
    : pkg.installed
      ? "Reinstall / Update"
      : "Install";

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
              className="mt-1 block rounded-md border border-bark/20 bg-white px-3 py-2 disabled:opacity-60"
              value={version}
              disabled={busy !== null}
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
            disabled={busy !== null}
            onClick={doPreview}
            className="btn-secondary"
          >
            {previewing ? "Previewing…" : "Preview"}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={doInstall}
            className="btn-primary min-w-[8.5rem]"
            aria-busy={installing}
          >
            {installLabel}
          </button>
        </div>
        {pkg.installed && !installing && (
          <p className="mt-2 text-xs text-bark/60">Installed version: {pkg.installed_version}</p>
        )}

        {installing && (
          <div
            className="mt-4 rounded-xl border border-sea/25 bg-sea/5 px-4 py-3 text-sm"
            role="status"
            aria-live="polite"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-medium text-sea">
                Installing {pkg.full_name}
                {version ? ` ${version}` : ""}…
              </p>
              <p className="tabular-nums text-xs text-bark/60">{formatElapsed(elapsed)}</p>
            </div>
            <p className="mt-1 text-bark/80">{statusLine}</p>
            <p className="mt-2 text-xs text-bark/55">
              Large packs (map tiles, web assets) can take a minute or more.
            </p>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-mist">
              <div className="install-progress-bar h-full rounded-full bg-sea/70" />
            </div>
          </div>
        )}

        {previewing && statusLine && (
          <p className="mt-3 text-sm text-bark/70" role="status" aria-live="polite">
            {statusLine}
          </p>
        )}

        {error && (
          <div
            className="mt-4 rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger"
            role="alert"
          >
            <p className="font-medium">Install failed</p>
            <p className="mt-1 break-words text-bark/80">{error}</p>
          </div>
        )}

        {installed && installed.length > 0 && (
          <div
            className="mt-4 rounded-xl border border-moss/30 bg-moss/5 px-4 py-3 text-sm"
            role="status"
            aria-live="polite"
          >
            <p className="font-medium text-moss-deep">
              Installed {installed.length} package{installed.length === 1 ? "" : "s"}
            </p>
            <ul className="mt-2 space-y-1 text-bark/80">
              {installed.map((p) => (
                <li key={`${p.source}:${p.full_name}`}>
                  <span className="font-medium text-ink">{p.full_name}</span>
                  {p.version ? ` ${p.version}` : ""}
                  {p.live_only ? (
                    <span className="ml-2 rounded bg-ember/15 px-1.5 py-0.5 text-[10px] uppercase text-ember">
                      live-only
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-bark/70">
              A server restart (or bootstrap apply) may be required before the game loads the new
              files.
            </p>
            <Link to="/installed" className="mt-2 inline-block text-sea hover:underline">
              View installed mods →
            </Link>
          </div>
        )}

        {preview && !installing && (
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
