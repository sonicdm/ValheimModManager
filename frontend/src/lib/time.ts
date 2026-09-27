/** Parse API timestamps and format them in the browser's local timezone/locale. */

export function parseApiDate(value: string | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  let s = String(value).trim();
  if (!s) return null;
  // Normalize "YYYY-MM-DD HH:MM:SS" → ISO
  if (/^\d{4}-\d{2}-\d{2} /.test(s)) {
    s = s.replace(" ", "T");
  }
  // Naive ISO datetimes from the API are UTC — append Z so JS doesn't treat them as local.
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s) && !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(s)) {
    s += "Z";
  }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatLocalDateTime(value: string | Date | null | undefined): string {
  const d = parseApiDate(value);
  if (!d) return "";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatLocalDate(value: string | Date | null | undefined): string {
  const d = parseApiDate(value);
  if (!d) return "";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
