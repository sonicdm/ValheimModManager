import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import CategoryMultiSelect, { CategoryInfo } from "../components/CategoryMultiSelect";
import { StatusToast, useStatusToast } from "../components/StatusToast";
import { api, Package } from "../api/client";

type CategoriesResponse = {
  categories: CategoryInfo[];
  default_include: string[];
  default_exclude: string[];
};

const FALLBACK_SERVER_INCLUDE = [
  "Server-side",
  "Server-only",
  "Client & Server",
  "Client (& Server)",
];

export default function DiscoverPage() {
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [include, setInclude] = useState<string[]>(FALLBACK_SERVER_INCLUDE);
  const [exclude, setExclude] = useState<string[]>([]);
  const [sort, setSort] = useState("downloads");
  const [categories, setCategories] = useState<CategoryInfo[]>([]);
  const [defaultsReady, setDefaultsReady] = useState(false);
  const [packages, setPackages] = useState<Package[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [indexBusy, setIndexBusy] = useState(false);
  const toast = useStatusToast();

  const loadCategories = useCallback(async (src: string) => {
    const params = new URLSearchParams();
    if (src) params.set("source", src);
    const qs = params.toString();
    const data = await api.get<CategoriesResponse>(
      `/api/packages/categories${qs ? `?${qs}` : ""}`,
    );
    setCategories(data.categories || []);
    return data;
  }, []);

  const search = useCallback(
    async (nextQ = q) => {
      setBusy(true);
      setError(null);
      try {
        const params = new URLSearchParams({ q: nextQ, sort, limit: "40" });
        if (source) params.set("source", source);
        for (const c of include) params.append("include", c);
        for (const c of exclude) params.append("exclude", c);
        setPackages(await api.get<Package[]>(`/api/packages?${params}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Search failed");
      } finally {
        setBusy(false);
      }
    },
    [q, sort, source, include, exclude],
  );

  useEffect(() => {
    loadCategories("")
      .then((data) => {
        if (data.default_include?.length) setInclude(data.default_include);
        if (data.default_exclude) setExclude(data.default_exclude);
        setDefaultsReady(true);
      })
      .catch(() => setDefaultsReady(true));
  }, [loadCategories]);

  useEffect(() => {
    if (!defaultsReady) return;
    loadCategories(source).catch(() => setCategories([]));
  }, [source, defaultsReady, loadCategories]);

  useEffect(() => {
    if (!defaultsReady) return;
    search("").catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultsReady, source, include, exclude, sort]);

  function setIncludeExclusive(next: string[]) {
    setInclude(next);
    if (next.length) setExclude((ex) => ex.filter((c) => !next.includes(c)));
  }

  function setExcludeExclusive(next: string[]) {
    setExclude(next);
    if (next.length) setInclude((inc) => inc.filter((c) => !next.includes(c)));
  }

  async function refreshIndex() {
    setIndexBusy(true);
    setError(null);
    toast.showBusy("Updating Thunderstore / Hexium indexes…");
    try {
      const result = await api.post<{ counts?: Record<string, number> }>("/api/packages/refresh");
      await loadCategories(source);
      await search();
      const counts = result.counts || {};
      const summary = Object.entries(counts)
        .map(([src, n]) => `${src}: ${n}`)
        .join(", ");
      toast.showOk(summary ? `Indexes updated (${summary}).` : "Indexes updated.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Refresh failed";
      setError(msg);
      toast.showError(msg);
    } finally {
      setIndexBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <StatusToast message={toast.message} tone={toast.tone} busy={toast.busy} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-3xl">Discover</h2>
          <p className="text-sm text-bark/70">
            Browse Thunderstore and Hexium. Defaults to server-side categories for dedicated
            servers.
          </p>
        </div>
        <button
          type="button"
          disabled={busy || indexBusy}
          onClick={refreshIndex}
          className="btn-secondary"
        >
          {indexBusy ? "Updating indexes…" : "Refresh indexes"}
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
          placeholder='Search mods — "exact phrase" or fuzzy words'
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <CategoryMultiSelect
          mode="include"
          options={categories}
          selected={include}
          onChange={setIncludeExclusive}
          disabled={busy}
        />
        <CategoryMultiSelect
          mode="exclude"
          options={categories}
          selected={exclude}
          onChange={setExcludeExclusive}
          disabled={busy}
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
        <button type="submit" className="btn-primary">
          Search
        </button>
      </form>

      {(include.length > 0 || exclude.length > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-bark/65">
          {include.map((c) => (
            <button
              key={`in-${c}`}
              type="button"
              className="rounded-full bg-moss/15 px-2 py-0.5 text-moss-deep hover:bg-moss/25"
              onClick={() => setIncludeExclusive(include.filter((x) => x !== c))}
              title="Remove include"
            >
              + {c} ×
            </button>
          ))}
          {exclude.map((c) => (
            <button
              key={`ex-${c}`}
              type="button"
              className="rounded-full bg-danger/10 px-2 py-0.5 text-danger hover:bg-danger/15"
              onClick={() => setExcludeExclusive(exclude.filter((x) => x !== c))}
              title="Remove exclude"
            >
              − {c} ×
            </button>
          ))}
          <button
            type="button"
            className="text-sea hover:underline"
            onClick={() => {
              setInclude([]);
              setExclude([]);
            }}
          >
            Clear filters
          </button>
          <button
            type="button"
            className="text-sea hover:underline"
            onClick={() => {
              setInclude(FALLBACK_SERVER_INCLUDE);
              setExclude([]);
            }}
          >
            Reset to server-side
          </button>
        </div>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}
      {busy && packages.length === 0 && <p className="text-sm text-bark/60">Loading…</p>}
      {!busy && packages.length === 0 && !error && (
        <p className="text-sm text-bark/60">No packages match these filters.</p>
      )}

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
              {(pkg.categories || []).length > 0 && (
                <p className="mt-1 truncate text-[10px] text-bark/50">
                  {(pkg.categories || []).slice(0, 4).join(" · ")}
                </p>
              )}
              <p className="mt-1 line-clamp-2 text-xs text-bark/70">{pkg.description}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
