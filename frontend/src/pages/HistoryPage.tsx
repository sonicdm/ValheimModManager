import { useEffect, useState } from "react";
import { api, Activity } from "../api/client";

export default function HistoryPage() {
  const [rows, setRows] = useState<Activity[]>([]);
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const params = new URLSearchParams({ limit: "200" });
    if (q) params.set("q", q);
    if (action) params.set("action", action);
    setRows(await api.get<Activity[]>(`/api/activity?${params}`));
  }

  useEffect(() => {
    load().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <h2 className="font-display text-3xl">Update history</h2>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          load().catch((err) => setError(err.message));
        }}
      >
        <input
          className="rounded-md border border-bark/20 bg-paper px-3 py-2"
          placeholder="Search…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          className="rounded-md border border-bark/20 bg-paper px-3 py-2"
          value={action}
          onChange={(e) => setAction(e.target.value)}
        >
          <option value="">All actions</option>
          {[
            "scan",
            "install",
            "uninstall",
            "update",
            "config_edit",
            "backup",
            "rollback",
            "maintenance",
            "restart",
            "login",
          ].map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <button type="submit" className="rounded-md bg-moss px-3 py-2 text-paper">
          Filter
        </button>
      </form>
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="overflow-x-auto rounded-2xl border border-bark/10 bg-paper/80">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-bark/10 text-xs uppercase text-bark/60">
            <tr>
              <th className="px-4 py-3">When</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Package</th>
              <th className="px-4 py-3">Result</th>
              <th className="px-4 py-3">Message</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-bark/5">
                <td className="px-4 py-2 whitespace-nowrap">
                  {new Date(r.timestamp).toLocaleString()}
                </td>
                <td className="px-4 py-2">{r.action}</td>
                <td className="px-4 py-2">{r.package || "—"}</td>
                <td className="px-4 py-2">{r.result}</td>
                <td className="px-4 py-2 text-bark/70">{r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
