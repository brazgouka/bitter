"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, Report } from "@/lib/api";
import ReportView from "../../ReportView";

export default function ReportDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getReport(id)
      .then(setReport)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!report) return <div className="error">{error || "Not found"}</div>;

  return (
    <div>
      <p className="sub">
        <Link href="/dashboard/reports">← Reports</Link>
      </p>
      <h1>Digest — {report.period_date}</h1>
      <ReportView report={report} />
    </div>
  );
}
