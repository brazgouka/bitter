"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, Repository } from "@/lib/api";

export default function ReposPage() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRepos(await api.listRepos());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function sync() {
    setSyncing(true);
    setError(null);
    try {
      setRepos(await api.syncRepos());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function toggle(repo: Repository) {
    setBusyId(repo.id);
    setError(null);
    try {
      const updated = await api.setMonitored(repo.id, !repo.monitored);
      setRepos((rs) =>
        rs.map((r) =>
          r.id === repo.id
            ? { ...r, monitored: updated.monitored, component_count: updated.component_count }
            : r
        )
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <div className="empty">Loading…</div>;

  return (
    <div>
      <div className="row">
        <div>
          <h1>Repositories</h1>
          <p className="sub">
            Toggle monitoring to fingerprint a repo&apos;s stack. Enabling scans
            it immediately.
          </p>
        </div>
        <button onClick={sync} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync repositories"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {repos.length === 0 ? (
        <div className="empty">
          No repositories yet. Click <strong>Sync repositories</strong> to
          import them from GitHub and, if connected in Settings, GitLab.
        </div>
      ) : (
        <div className="card">
          {repos.map((repo) => (
            <div className="list-row" key={repo.id}>
              <div>
                <span
                  className="badge"
                  style={{ marginRight: 8 }}
                  title={repo.provider === "gitlab" ? "GitLab" : "GitHub"}
                >
                  {repo.provider === "gitlab" ? "GL" : "GH"}
                </span>
                <Link href={`/dashboard/repos/${repo.id}`}>
                  {repo.full_name}
                </Link>
                {repo.private && (
                  <span className="badge private" style={{ marginLeft: 8 }}>
                    private
                  </span>
                )}
                <div className="muted" style={{ fontSize: 13 }}>
                  {repo.component_count} components
                  {repo.last_scanned_at
                    ? ` · scanned ${new Date(
                        repo.last_scanned_at
                      ).toLocaleDateString()}`
                    : " · never scanned"}
                </div>
              </div>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={repo.monitored}
                  disabled={busyId === repo.id}
                  onChange={() => toggle(repo)}
                />
                {busyId === repo.id
                  ? "…"
                  : repo.monitored
                  ? "Monitored"
                  : "Off"}
              </label>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
