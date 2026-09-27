import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Package } from "../api/client";

export default function DiscoverPage() {
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("downloads");
  const [packages, setPackages] = useState<Package[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function search(nextQ = q) {
    setBusy(true);
    setError(null);
    try {
      const params = new URLSearchParams({ q: nextQ, sort, limit: "40" });
      if (source) params.set("source", source);
      setPackages(await api.get<Package[]>(`/api/packages?${params}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    search("").catch(() => undefined);
    // initial load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshIndex() {
    setBusy(true);
    try {
      await api.post("/api/packages/refresh");
      await search();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Discover</h2>
          <p className="text-sm text-bark/70">Browse Thunderstore and Hexium Valheim packages.</p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={refreshIndex}
          className="rounded-md border border-bark/20 bg-paper px-3 py-2 text-sm"
        >
          Refresh indexes
        </button>
      </div>

      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <input
          className="min-w-[16rem] flex-1 rounded-md border border-bark/20 bg-paper px-3 py-2"
          placeholder="Search by name or author"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          className="rounded-md border border-bark/20 bg-paper px-3 py-2"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">All sources</option>
          <option value="thunderstore">Thunderstore</option>
          <option value="hexium">Hexium</option>
        </select>
        <select
          className="rounded-md border border-bark/20 bg-paper px-3 py-2"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
        >
          <option value="downloads">Downloads</option>
          <option value="updated">Updated</option>
          <option value="rating">Rating</option>
          <option value="name">Name</option>
        </select>
        <button type="submit" className="rounded-md bg-moss px-4 py-2 text-paper">
          Search
        </button>
      </form>

      {error && <p className="text-sm text-danger">{error}</p>}
      {busy && packages.length === 0 && <p className="text-sm text-bark/60">Loading…</p>}

      <div className="grid gap-3 sm:grid-cols-2">
        {packages.map((pkg) => (
          <Link
            key={`${pkg.source}:${pkg.full_name}`}
            to={`/discover/${pkg.source}/${encodeURIComponent(pkg.full_name)}`}
            className="flex gap-3 rounded-2xl border border-bark/10 bg-paper/80 p-4 hover:border-moss/40"
          >
            {pkg.icon_url ? (
              <img src={pkg.icon_url} alt="" className="h-14 w-14 rounded-lg object-cover" />
            ) : (
              <div className="grid h-14 w-14 place-items-center rounded-lg bg-mist text-xs text-bark/50">
                mod
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="truncate font-medium">{pkg.name}</p>
                <span className="rounded-full bg-mist px-2 py-0.5 text-[10px] uppercase tracking-wide">
                  {pkg.source}
                </span>
                {pkg.installed && (
                  <span className="rounded-full bg-moss/15 px-2 py-0.5 text-[10px] text-moss-deep">
                    installed
                  </span>
                )}
              </div>
              <p className="truncate text-xs text-bark/60">
                {pkg.owner} · {pkg.latest_version} · {pkg.downloads.toLocaleString()} downloads
              </p>
              <p className="mt-1 line-clamp-2 text-xs text-bark/70">{pkg.description}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
