"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ReportSummary } from "@/lib/api";

export default function ReportsPage() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listReports()
      .then(setReports)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="empty">Loading…</div>;

  return (
    <div>
      <h1>Reports</h1>
      <p className="sub">Your daily digest history.</p>

      {error && <div className="error">{error}</div>}

      {reports.length === 0 ? (
        <div className="empty">No reports yet.</div>
      ) : (
        <div className="card">
          {reports.map((r) => (
            <div className="list-row" key={r.id}>
              <div>
                <Link href={`/dashboard/reports/${r.id}`}>{r.period_date}</Link>
                <div className="muted" style={{ fontSize: 13 }}>
                  {r.summary}
                </div>
              </div>
              <div className="counts">
                <span className="pill c">{r.critical_count}</span>
                <span className="pill m">{r.medium_count}</span>
                <span className="pill i">{r.ignore_count}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
