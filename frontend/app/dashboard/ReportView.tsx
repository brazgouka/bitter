"use client";

import { useMemo, useState } from "react";
import { api, Report, ReportItem, Severity } from "@/lib/api";

const GROUPS: { key: Severity; label: string }[] = [
  { key: "critical", label: "Critical" },
  { key: "medium", label: "Medium" },
  { key: "ignore", label: "Ignore" },
];

function FindingCard({
  item,
  onItemChange,
}: {
  item: ReportItem;
  onItemChange: (item: ReportItem) => void;
}) {
  const [muting, setMuting] = useState(false);
  const [muted, setMuted] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function mute() {
    setMuting(true);
    setError(null);
    try {
      await api.createMute(item.technology);
      setMuted(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setMuting(false);
    }
  }

  async function proposeFix() {
    setFixing(true);
    setError(null);
    try {
      const updated = await api.proposeFix(item.id);
      onItemChange(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFixing(false);
    }
  }

  return (
    <div className={`card finding ${item.severity}`}>
      <div className="spread" style={{ justifyContent: "space-between" }}>
        <h3>{item.title}</h3>
        <span className={`badge sev-${item.severity}`}>{item.severity}</span>
      </div>
      <div className="meta spread" style={{ marginBottom: 6 }}>
        <span>{item.technology}</span>
        {item.repository_full_name && (
          <span className="badge eco">{item.repository_full_name}</span>
        )}
      </div>
      <div>{item.body}</div>
      {item.reasons.length > 1 && (
        <ul className="reasons">
          {item.reasons.map((r, i) => (
            <li key={i}>
              {r.url ? (
                <a href={r.url} target="_blank" rel="noreferrer">
                  {r.title}
                </a>
              ) : (
                r.title
              )}
            </li>
          ))}
        </ul>
      )}
      {item.affected_files.length > 1 && (
        <div className="spread" style={{ marginTop: 8 }}>
          {item.affected_files.map((f) => (
            <span key={f} className="badge eco mono">
              {f}
            </span>
          ))}
        </div>
      )}
      {item.action && <div className="action">→ {item.action}</div>}

      <div className="spread" style={{ marginTop: 10 }}>
        <button onClick={mute} disabled={muting || muted}>
          {muted ? "Muted ✓" : muting ? "Muting…" : `Mute ${item.technology}`}
        </button>
        {item.fix_pr_url ? (
          <a className="btn btn-primary" href={item.fix_pr_url} target="_blank" rel="noreferrer">
            View PR ↗
          </a>
        ) : item.can_propose_fix ? (
          <button onClick={proposeFix} disabled={fixing}>
            {fixing ? "Opening PR…" : "Propose fix (open PR)"}
          </button>
        ) : null}
      </div>
      {muted && (
        <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
          {item.technology} will be excluded from future reports.
        </div>
      )}
      {error && (
        <div className="error" style={{ marginTop: 8, marginBottom: 0 }}>
          {error}
        </div>
      )}
    </div>
  );
}

export default function ReportView({ report }: { report: Report }) {
  const [items, setItems] = useState<ReportItem[]>(report.items);
  const [repoFilter, setRepoFilter] = useState<string>("all");
  const [severityFilter, setSeverityFilter] = useState<string>("all");

  const repos = useMemo(() => {
    const set = new Map<string, string>();
    for (const it of items) {
      if (it.repository_id != null && it.repository_full_name) {
        set.set(String(it.repository_id), it.repository_full_name);
      }
    }
    return [...set.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [items]);

  const filtered = items.filter((it) => {
    if (repoFilter !== "all" && String(it.repository_id) !== repoFilter) return false;
    if (severityFilter !== "all" && it.severity !== severityFilter) return false;
    return true;
  });

  function handleItemChange(updated: ReportItem) {
    setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)));
  }

  return (
    <div>
      <div className="card">
        <div className="counts" style={{ marginBottom: 8 }}>
          <span className="pill c">
            {items.filter((i) => i.severity === "critical").length} critical
          </span>
          <span className="pill m">
            {items.filter((i) => i.severity === "medium").length} medium
          </span>
          <span className="pill i">
            {items.filter((i) => i.severity === "ignore").length} ignore
          </span>
        </div>
        <div>{report.summary}</div>
      </div>

      {items.length > 0 && (
        <div className="spread" style={{ marginBottom: 16 }}>
          {repos.length > 1 && (
            <select value={repoFilter} onChange={(e) => setRepoFilter(e.target.value)}>
              <option value="all">All repositories ({items.length})</option>
              {repos.map(([id, name]) => (
                <option key={id} value={id}>
                  {name} ({items.filter((i) => String(i.repository_id) === id).length})
                </option>
              ))}
            </select>
          )}
          <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
            <option value="all">All severities</option>
            {GROUPS.map(({ key, label }) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
      )}

      {items.length === 0 && (
        <div className="empty">Nothing needs your attention today. ✅</div>
      )}
      {items.length > 0 && filtered.length === 0 && (
        <div className="empty">No findings match the current filters.</div>
      )}

      {GROUPS.map(({ key, label }) => {
        const groupItems = filtered.filter((i) => i.severity === key);
        if (groupItems.length === 0) return null;
        return (
          <div key={key}>
            <div className="group-title">{label}</div>
            {groupItems.map((it) => (
              <FindingCard key={it.id} item={it} onItemChange={handleItemChange} />
            ))}
          </div>
        );
      })}
    </div>
  );
}
