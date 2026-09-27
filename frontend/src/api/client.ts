let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function getCsrfToken() {
  return csrfToken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && init.method && init.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export type AuthStatus = {
  authenticated: boolean;
  username?: string | null;
  csrf_token?: string | null;
};

export type DashboardStats = {
  installed_count: number;
  unmanaged_count: number;
  updates_count: number;
  disabled_count: number;
  last_scan_at?: string | null;
  last_update_check_at?: string | null;
  maintenance_enabled: boolean;
  next_maintenance_window?: string | null;
  supervisor_status?: string | null;
  supervisor_configured: boolean;
  restart_required: boolean;
  recent_activity: Activity[];
  pending_updates: PendingUpdate[];
};

export type Activity = {
  id: number;
  timestamp: string;
  action: string;
  package?: string | null;
  source?: string | null;
  result: string;
  message?: string | null;
};

export type PendingUpdate = {
  id: number;
  source: string;
  full_name: string;
  current_version?: string | null;
  target_version: string;
  status: string;
  detected_at: string;
};

export type InstalledPackage = {
  id: number;
  source: string;
  full_name: string;
  name: string;
  owner?: string | null;
  version?: string | null;
  plugin_guid?: string | null;
  install_path: string;
  managed: boolean;
  enabled: boolean;
  pinned: boolean;
  auto_update: boolean;
  description?: string | null;
  icon_url?: string | null;
  package_url?: string | null;
  dependencies: string[];
  config_files: string[];
  owned_files: string[];
  update_available?: string | null;
};

export type Package = {
  source: string;
  name: string;
  full_name: string;
  owner: string;
  package_url?: string | null;
  description?: string | null;
  icon_url?: string | null;
  date_updated?: string | null;
  rating_score: number;
  downloads: number;
  categories: string[];
  installed: boolean;
  installed_version?: string | null;
  latest_version?: string | null;
  versions?: PackageVersion[];
  dependencies: string[];
};

export type PackageVersion = {
  version_number: string;
  download_url: string;
  dependencies: string[];
  description?: string | null;
  downloads: number;
};

export type ConfigFile = { path: string; name: string; size: number; modified_at?: string | null };
export type ConfigContent = {
  path: string;
  raw: string;
  structured: { sections: ConfigSection[] } | null;
  parse_ok: boolean;
  restart_required: boolean;
};
export type ConfigSection = {
  name: string;
  comment?: string | null;
  entries: {
    key: string;
    value: string;
    value_type: string;
    comment?: string | null;
    enum_values: string[];
  }[];
};

export type Backup = {
  id: number;
  created_at: string;
  label: string;
  reason: string;
  path: string;
  package_names: string[];
  notes?: string | null;
  success: boolean;
};
