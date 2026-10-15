"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, Report } from "@/lib/api";
import ReportView from "./ReportView";

export default function Overview() {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.latestReport());
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setReport(null);
      else setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function runNow() {
    setRunning(true);
    setError(null);
    try {
      await api.runNow();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <div className="empty">Loading…</div>;

  return (
    <div>
      <div className="row">
        <div>
          <h1>Overview</h1>
          <p className="sub">
            {report
              ? `Latest digest — ${report.period_date}`
              : "No digest generated yet."}
          </p>
        </div>
        <button className="btn-primary" onClick={runNow} disabled={running}>
          {running ? "Analyzing…" : "Run analysis now"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {!report && (
        <div className="empty">
          No report yet. Enable a repository under{" "}
          <Link href="/dashboard/repos">Repositories</Link>, then run an
          analysis.
        </div>
      )}

      {report && <ReportView report={report} />}
    </div>
  );
}
