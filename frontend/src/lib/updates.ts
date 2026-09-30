export type ApplyResultRow = {
  full_name: string;
  ok: boolean;
  version?: string;
  error?: string;
};

export type ApplyResult = {
  results: ApplyResultRow[];
  restart?: { ok?: boolean; message?: string } | null;
};

/** Human-readable summary of POST /api/updates/apply, including failure reasons. */
export function summarizeApply(data: ApplyResult): string {
  const okRows = data.results.filter((r) => r.ok);
  const failRows = data.results.filter((r) => !r.ok);
  const lines: string[] = [];

  if (okRows.length) {
    lines.push(
      `Updated ${okRows.length} package${okRows.length === 1 ? "" : "s"}` +
        (okRows.length <= 3 ? `: ${okRows.map((r) => r.full_name).join(", ")}` : ""),
    );
  } else if (!failRows.length) {
    lines.push("No packages updated");
  }

  for (const r of failRows) {
    const why = (r.error || "unknown error").trim();
    lines.push(`Failed ${r.full_name}: ${why}`);
  }

  if (data.restart?.ok) lines.push("Server restarted");
  else if (okRows.length) lines.push("Restart + sync when ready");

  return lines.join("\n");
}
