"use client";

import { useEffect, useState } from "react";
import { api, Mute, User } from "@/lib/api";

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [mutes, setMutes] = useState<Mute[]>([]);
  const [loading, setLoading] = useState(true);
  const [tech, setTech] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [gitlabToken, setGitlabToken] = useState("");
  const [gitlabBusy, setGitlabBusy] = useState(false);

  useEffect(() => {
    Promise.all([api.me(), api.listMutes()])
      .then(([u, m]) => {
        setUser(u);
        setMutes(m);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  async function toggleEmail() {
    if (!user) return;
    try {
      const updated = await api.updateSettings(!user.email_opt_in);
      setUser(updated);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function addMute() {
    const value = tech.trim();
    if (!value) return;
    try {
      const m = await api.createMute(value);
      setMutes((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m]));
      setTech("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function removeMute(id: number) {
    try {
      await api.deleteMute(id);
      setMutes((ms) => ms.filter((m) => m.id !== id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function connectGitLab() {
    const value = gitlabToken.trim();
    if (!value) return;
    setGitlabBusy(true);
    setError(null);
    try {
      const updated = await api.connectGitLab(value);
      setUser(updated);
      setGitlabToken("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGitlabBusy(false);
    }
  }

  async function disconnectGitLab() {
    setGitlabBusy(true);
    setError(null);
    try {
      setUser(await api.disconnectGitLab());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGitlabBusy(false);
    }
  }

  if (loading) return <div className="empty">Loading…</div>;

  return (
    <div>
      <h1>Settings</h1>
      <p className="sub">Control how Bitter reports to you.</p>

      {error && <div className="error">{error}</div>}

      <div className="group-title">Email digest</div>
      <div className="card">
        <label className="toggle">
          <input
            type="checkbox"
            checked={!!user?.email_opt_in}
            onChange={toggleEmail}
          />
          Send me the daily digest
          {user?.email ? ` at ${user.email}` : " (no email on file)"}
        </label>
      </div>

      <div className="group-title">GitLab</div>
      <div className="card">
        {user?.gitlab_connected ? (
          <div className="spread">
            <span>
              <span className="badge" style={{ marginRight: 8 }}>
                connected
              </span>
              Repositories from GitLab are included when you sync.
            </span>
            <button onClick={disconnectGitLab} disabled={gitlabBusy}>
              {gitlabBusy ? "…" : "Disconnect"}
            </button>
          </div>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              Paste a personal access token (scopes: <span className="mono">read_api</span>,{" "}
              <span className="mono">write_repository</span>) to import and monitor GitLab
              repositories alongside GitHub.
            </p>
            <div className="spread">
              <input
                type="password"
                placeholder="glpat-…"
                value={gitlabToken}
                onChange={(e) => setGitlabToken(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && connectGitLab()}
              />
              <button onClick={connectGitLab} disabled={gitlabBusy || !gitlabToken.trim()}>
                {gitlabBusy ? "Connecting…" : "Connect"}
              </button>
            </div>
          </>
        )}
      </div>

      <div className="group-title">Muted technologies</div>
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          Muted technologies are excluded from all findings.
        </p>
        <div className="spread" style={{ marginBottom: 12 }}>
          <input
            type="text"
            placeholder="e.g. react"
            value={tech}
            onChange={(e) => setTech(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addMute()}
          />
          <button onClick={addMute}>Mute</button>
        </div>
        {mutes.length === 0 ? (
          <div className="muted">No muted technologies.</div>
        ) : (
          mutes.map((m) => (
            <div className="list-row" key={m.id}>
              <span className="mono">{m.technology}</span>
              <button onClick={() => removeMute(m.id)}>Unmute</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
