"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, Component, RepositoryDetail } from "@/lib/api";

function groupByEcosystem(components: Component[]) {
  const map = new Map<string, Component[]>();
  for (const c of components) {
    const list = map.get(c.ecosystem) || [];
    list.push(c);
    map.set(c.ecosystem, list);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export default function RepoDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const [repo, setRepo] = useState<RepositoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRepo(await api.getRepo(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function scan() {
    setScanning(true);
    setError(null);
    try {
      setRepo(await api.scanRepo(id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  }

  if (loading) return <div className="empty">Loading…</div>;
  if (!repo) return <div className="error">{error || "Not found"}</div>;

  const groups = groupByEcosystem(repo.components);

  return (
    <div>
      <p className="sub">
        <Link href="/dashboard/repos">← Repositories</Link>
      </p>
      <div className="row">
        <div>
          <h1>{repo.full_name}</h1>
          <p className="sub">
            {repo.monitored ? "Monitored" : "Not monitored"} ·{" "}
            {repo.components.length} components detected
          </p>
        </div>
        <button onClick={scan} disabled={scanning}>
          {scanning ? "Scanning…" : "Rescan"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {groups.length === 0 ? (
        <div className="empty">
          No components detected. Rescan, or enable monitoring first.
        </div>
      ) : (
        groups.map(([eco, comps]) => (
          <div key={eco}>
            <div className="group-title">{eco}</div>
            <div className="card">
              {comps.map((c) => (
                <div className="list-row" key={c.id}>
                  <div>
                    <span className="mono">{c.name}</span>{" "}
                    {c.version && (
                      <span className="badge eco">{c.version}</span>
                    )}
                  </div>
                  <div className="muted mono">{c.source_file}</div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
