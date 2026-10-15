"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .me()
      .then(() => router.replace("/dashboard"))
      .catch(() => setLoading(false));
  }, [router]);

  async function devLogin() {
    setBusy(true);
    try {
      await api.devLogin();
      router.replace("/dashboard");
    } catch {
      setBusy(false);
    }
  }

  if (loading) return <div className="center">Loading…</div>;

  return (
    <div className="hero">
      <h1>◆ Bitter</h1>
      <p className="tagline">
        Your AI engineering advisor. Bitter watches your stack across every repo
        and tells you <em>only</em> what needs your attention — nothing else.
      </p>

      <div className="example">
        <div className="group-title" style={{ marginTop: 0 }}>
          Example morning digest
        </div>
        <p>
          <span className="badge sev-critical">Critical</span>&nbsp; Kubernetes
          API in <span className="mono">payments-api</span> is removed in v1.22 —
          migration required.
        </p>
        <p>
          <span className="badge sev-medium">Medium</span>&nbsp; Next.js 14
          available (major upgrade from 12).
        </p>
        <p style={{ marginBottom: 0 }}>
          <span className="badge sev-ignore">Ignore</span>&nbsp; FastAPI patch
          release — no action needed.
        </p>
      </div>

      <div className="spread">
        <a className="btn btn-github" href={api.loginUrl()}>
          Sign in with GitHub
        </a>
        <button onClick={devLogin} disabled={busy}>
          {busy ? "Starting…" : "Try the demo"}
        </button>
      </div>
    </div>
  );
}
