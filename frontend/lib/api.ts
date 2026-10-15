/**
 * Typed client for the Bitter backend. Every call sends the session cookie
 * (`credentials: 'include'`); the API base comes from NEXT_PUBLIC_API_URL.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Severity = "critical" | "medium" | "ignore";

export interface User {
  id: number;
  login: string;
  name?: string | null;
  email?: string | null;
  avatar_url?: string | null;
  email_opt_in: boolean;
  gitlab_connected: boolean;
}

export interface Component {
  id: number;
  ecosystem: string;
  name: string;
  version?: string | null;
  source_file: string;
}

export interface Repository {
  id: number;
  provider: "github" | "gitlab";
  full_name: string;
  default_branch: string;
  private: boolean;
  monitored: boolean;
  last_scanned_at?: string | null;
  component_count: number;
}

export interface RepositoryDetail extends Repository {
  components: Component[];
}

export interface FixReason {
  title: string;
  detail: string;
  url?: string | null;
  fixed_version?: string | null;
}

export interface ReportItem {
  id: number;
  severity: Severity;
  technology: string;
  title: string;
  body: string;
  action?: string | null;
  repository_id?: number | null;
  repository_full_name?: string | null;
  event_id?: number | null;
  fixed_version?: string | null;
  fix_pr_url?: string | null;
  can_propose_fix: boolean;
  reasons: FixReason[];
  affected_files: string[];
}

export interface Report {
  id: number;
  period_date: string;
  generated_at: string;
  status: string;
  summary?: string | null;
  items: ReportItem[];
}

export interface ReportSummary {
  id: number;
  period_date: string;
  generated_at: string;
  status: string;
  summary?: string | null;
  critical_count: number;
  medium_count: number;
  ignore_count: number;
}

export interface Mute {
  id: number;
  technology: string;
  reason?: string | null;
}

export interface RunResult {
  report_id: number;
  period_date: string;
  critical_count: number;
  medium_count: number;
  ignore_count: number;
  message: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : (undefined as T);
}

export const api = {
  loginUrl: () => `${API_URL}/api/auth/login`,
  me: () => req<User>("/api/auth/me"),
  devLogin: () => req<User>("/api/auth/dev-login", { method: "POST" }),
  logout: () => req<{ status: string }>("/api/auth/logout", { method: "POST" }),

  listRepos: () => req<Repository[]>("/api/repos"),
  syncRepos: () => req<Repository[]>("/api/repos/sync", { method: "POST" }),
  getRepo: (id: number) => req<RepositoryDetail>(`/api/repos/${id}`),
  setMonitored: (id: number, monitored: boolean) =>
    req<RepositoryDetail>(`/api/repos/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ monitored }),
    }),
  scanRepo: (id: number) =>
    req<RepositoryDetail>(`/api/repos/${id}/scan`, { method: "POST" }),

  runNow: () => req<RunResult>("/api/run", { method: "POST" }),
  listReports: () => req<ReportSummary[]>("/api/reports"),
  latestReport: () => req<Report>("/api/reports/latest"),
  getReport: (id: number) => req<Report>(`/api/reports/${id}`),
  proposeFix: (itemId: number) =>
    req<ReportItem>(`/api/reports/items/${itemId}/fix`, { method: "POST" }),

  updateSettings: (email_opt_in: boolean) =>
    req<User>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ email_opt_in }),
    }),
  connectGitLab: (token: string) =>
    req<User>("/api/integrations/gitlab", {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  disconnectGitLab: () =>
    req<User>("/api/integrations/gitlab", { method: "DELETE" }),
  listMutes: () => req<Mute[]>("/api/mutes"),
  createMute: (technology: string, reason?: string) =>
    req<Mute>("/api/mutes", {
      method: "POST",
      body: JSON.stringify({ technology, reason }),
    }),
  deleteMute: (id: number) =>
    req<{ status: string }>(`/api/mutes/${id}`, { method: "DELETE" }),
};
