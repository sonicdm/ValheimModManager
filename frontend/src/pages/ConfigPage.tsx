import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ConfigContent, ConfigFile } from "../api/client";

export default function ConfigPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("file");
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [content, setContent] = useState<ConfigContent | null>(null);
  const [mode, setMode] = useState<"structured" | "raw">("structured");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    api
      .get<ConfigFile[]>("/api/configs")
      .then(setFiles)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) {
      setContent(null);
      return;
    }
    api
      .get<ConfigContent>(`/api/configs/${encodeURIComponent(selected)}`)
      .then((c) => {
        setContent(c);
        setRaw(c.raw);
        setMode(c.parse_ok ? "structured" : "raw");
      })
      .catch((e) => setError(e.message));
  }, [selected]);

  const filtered = useMemo(() => {
    const q = filter.toLowerCase();
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, filter]);

  async function save() {
    if (!selected || !content) return;
    setError(null);
    setMessage(null);
    try {
      const body = mode === "raw" ? { raw } : { structured: content.structured };
      const saved = await api.put<ConfigContent>(
        `/api/configs/${encodeURIComponent(selected)}`,
        body,
      );
      setContent(saved);
      setRaw(saved.raw);
      setMessage("Saved. Restart required for changes to apply in-game.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
      <aside className="rounded-2xl border border-bark/10 bg-paper/80 p-3">
        <h2 className="font-display text-2xl">Configs</h2>
        <input
          className="mt-2 w-full rounded border border-bark/20 px-2 py-1.5 text-sm"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <ul className="mt-3 max-h-[70vh] space-y-1 overflow-auto text-sm">
          {filtered.map((f) => (
            <li key={f.name}>
              <button
                type="button"
                className={[
                  "w-full rounded px-2 py-1.5 text-left",
                  selected === f.name ? "bg-moss text-paper" : "hover:bg-mist",
                ].join(" ")}
                onClick={() => setParams({ file: f.name })}
              >
                {f.name}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="rounded-2xl border border-bark/10 bg-paper/80 p-5">
        {!selected && <p className="text-bark/60">Select a configuration file.</p>}
        {selected && content && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-display text-3xl">{content.path}</h3>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded border border-bark/20 px-2 py-1 text-xs"
                  onClick={() => setMode("structured")}
                  disabled={!content.parse_ok}
                >
                  Structured
                </button>
                <button
                  type="button"
                  className="rounded border border-bark/20 px-2 py-1 text-xs"
                  onClick={() => setMode("raw")}
                >
                  Raw
                </button>
                <button
                  type="button"
                  className="rounded bg-moss px-3 py-1.5 text-sm text-paper"
                  onClick={save}
                >
                  Save
                </button>
              </div>
            </div>
            {error && <p className="mt-2 text-sm text-danger">{error}</p>}
            {message && <p className="mt-2 text-sm text-moss-deep">{message}</p>}

            {mode === "raw" ? (
              <textarea
                className="mt-4 h-[60vh] w-full rounded-xl border border-bark/15 bg-white p-3 font-mono text-xs"
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
              />
            ) : (
              <div className="mt-4 space-y-6">
                {(content.structured?.sections || []).map((section, si) => (
                  <div key={section.name}>
                    <h4 className="font-display text-xl">{section.name}</h4>
                    <div className="mt-2 space-y-3">
                      {section.entries.map((entry, ei) => (
                        <label key={entry.key} className="block text-sm">
                          <span className="font-medium">{entry.key}</span>
                          {entry.comment && (
                            <span className="mt-0.5 block whitespace-pre-wrap text-xs text-bark/50">
                              {entry.comment}
                            </span>
                          )}
                          {entry.value_type === "bool" ? (
                            <select
                              className="mt-1 rounded border border-bark/20 px-2 py-1.5"
                              value={entry.value}
                              onChange={(e) => {
                                const next = structuredClone(content);
                                next.structured!.sections[si].entries[ei].value = e.target.value;
                                setContent(next);
                              }}
                            >
                              <option value="true">Enabled</option>
                              <option value="false">Disabled</option>
                            </select>
                          ) : entry.value_type === "enum" && entry.enum_values.length ? (
                            <select
                              className="mt-1 rounded border border-bark/20 px-2 py-1.5"
                              value={entry.value}
                              onChange={(e) => {
                                const next = structuredClone(content);
                                next.structured!.sections[si].entries[ei].value = e.target.value;
                                setContent(next);
                              }}
                            >
                              {entry.enum_values.map((v) => (
                                <option key={v} value={v}>
                                  {v}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              className="mt-1 w-full max-w-md rounded border border-bark/20 px-2 py-1.5"
                              value={entry.value}
                              onChange={(e) => {
                                const next = structuredClone(content);
                                next.structured!.sections[si].entries[ei].value = e.target.value;
                                setContent(next);
                              }}
                            />
                          )}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
