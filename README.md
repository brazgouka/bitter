# ◆ Bitter — Engineering Intelligence Platform

[![CI](https://github.com/brazgouka/bitter/actions/workflows/ci.yml/badge.svg)](https://github.com/brazgouka/bitter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js 14](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)

Bitter is an **AI engineering advisor**. Point it at your GitHub repositories and
every morning it tells you *only what needs your attention* — critical security
advisories, breaking releases, deprecations, and removed APIs that actually
affect the technologies you use. Not a news feed. Not another dependency bot.

> **Critical** — Kubernetes API `networking.k8s.io/v1beta1 Ingress` in `payments-api` is removed in v1.22. Migration required.
> **Medium** — Next.js 14 is available (major upgrade from 12). Review breaking changes.
> **Ignore** — FastAPI patch release. No action needed.

---

## Features

- **Stack-aware triage** — only events that intersect the exact versions and APIs your repos use
- **GitHub & GitLab** — GitHub via OAuth, GitLab (incl. self-hosted) via personal access token
- **One-click fix PRs** — bumps the manifest (pip, npm, Maven, Kubernetes `apiVersion`) and,
  with an LLM backend, rewrites code that the upgrade breaks — all in a single PR/MR
- **Pluggable advisor** — Claude, any local OpenAI-compatible model (Ollama, LM Studio),
  or a deterministic heuristic that needs no network or keys
- **Daily digest** — scheduled run with in-app reports and email delivery
- **Runs offline** — a seeded demo exercises the full pipeline without any credentials

## How it works

```
Sign in (GitHub OAuth / GitLab PAT)
      │
      ▼
Repo sync ──▶ Stack detection ──▶ Correlation ──▶ Claude advisor ──▶ Daily report
   (GitHub API)  (manifest/IaC       (events ∩       (severity +        (Critical/
                  parsers)            your stack)     action)            Medium/Ignore)
                                          ▲                                  │
                        Ingestion ────────┘                                  ▼
                 (OSV · GitHub Releases · endoflife.date)            Dashboard + Email
```

1. **Stack detection** parses `requirements.txt`, `pyproject.toml`, `package.json`,
   `Dockerfile`, `pom.xml`, Kubernetes manifests, `*.tf`, and GitHub Actions
   workflows into a normalized list of `(ecosystem, name, version)`.
2. **Ingestion** pulls normalized change events from **OSV.dev** (security
   advisories), the **GitHub Releases API** (new versions), and **endoflife.date**
   (deprecation / EOL). Kubernetes removed-APIs come from a curated table.
3. **Correlation** intersects those events with your stack — matching versions,
   ranges, and API usage — to produce candidate findings.
4. **The advisor** (Claude, with a deterministic heuristic fallback) assigns each
   finding a **Critical / Medium / Ignore** severity and a concrete action, and
   writes a one-line daily summary.
5. **Delivery** — findings land in the dashboard and (optionally) a daily email
   digest, fired by a scheduler.
6. **Propose fix** — from any finding, open a PR/MR that bumps the affected
   manifests and (best-effort, LLM-backed) migrates breaking API usages.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 |
| Git hosts | GitHub (OAuth) · GitLab (PAT) |
| Database | SQLite (local default) · PostgreSQL (Docker) |
| AI | Claude (Anthropic) — heuristic fallback when no key |
| Frontend | Next.js 14 (App Router, TypeScript) |
| Scheduling | APScheduler |
| Delivery | In-app dashboard · email (console/SMTP) |

## Project layout

```
bitter/
├─ backend/
│  ├─ app/
│  │  ├─ main.py            FastAPI app + lifespan
│  │  ├─ api.py             all routers (auth, repos, reports, settings, run)
│  │  ├─ models.py          SQLAlchemy tables
│  │  ├─ pipeline.py        scan → ingest → correlate → advise → report
│  │  ├─ worker.py          APScheduler daily job
│  │  ├─ tech_registry.py   tech → release/EOL sources
│  │  └─ services/
│  │     ├─ github.py       OAuth + REST client
│  │     ├─ gitlab.py       PAT-based REST client
│  │     ├─ providers.py    GitHub/GitLab client selection
│  │     ├─ detection.py    manifest/IaC parsers
│  │     ├─ ingestion.py    OSV · GitHub Releases · endoflife
│  │     ├─ correlation.py  events ∩ stack (+ K8s removed APIs)
│  │     ├─ versioning.py   tolerant version parsing/comparison
│  │     ├─ fixer.py        "Propose fix" PR/MR generation
│  │     ├─ advisor.py      Claude scoring + heuristic fallback
│  │     ├─ report.py       report assembly
│  │     └─ email.py        digest rendering + delivery
│  ├─ scripts/
│  │  ├─ seed_demo.py       offline end-to-end demo
│  │  ├─ run_real.py        pipeline against real GitHub repos
│  │  └─ check_*.py         verify Anthropic / local-model setup
│  └─ tests/                detection, versioning, fixer, report tests
└─ frontend/                Next.js dashboard
```

---

## Quick start — Docker (full stack)

```bash
cp backend/.env.example .env      # optional: add GITHUB_* and ANTHROPIC_API_KEY
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend docs: http://localhost:8000/docs

Without GitHub/Anthropic keys you can still explore: click **Try the demo** on
the landing page (dev-login), and the advisor uses its heuristic fallback.

## Quick start — local (no Docker)

**Backend**

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # SQLite default needs no changes

# See the whole pipeline run offline against seeded demo data:
python scripts/seed_demo.py

# Or run the API:
uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev                        # http://localhost:3000
```

## Configuration

Set these in `backend/.env` (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite (default) or `postgresql+psycopg://…` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth app. Callback: `http://localhost:8000/api/auth/callback` |
| `ANTHROPIC_API_KEY` | Enables the Claude advisor (else heuristic fallback) |
| `ANTHROPIC_MODEL` | Default `claude-sonnet-5` |
| `ADVISOR_BACKEND` | `auto` · `claude` · `local` · `heuristic` |
| `LOCAL_MODEL_BASE_URL` / `LOCAL_MODEL` | Local OpenAI-compatible server (for `local`) |
| `EMAIL_BACKEND` | `console` (default, prints digests) or `smtp` |
| `SESSION_SECRET` | Signs the session cookie — set a long random value |

### GitHub OAuth setup

1. Create an OAuth app at https://github.com/settings/developers
2. Authorization callback URL: `http://localhost:8000/api/auth/callback`
3. Put the client ID/secret in `backend/.env`.

### Free local model — no API key (Ollama)

The advisor can run on a free local LLM instead of Claude. Any OpenAI-compatible
server works; [Ollama](https://ollama.com) is easiest:

```bash
# 1. Install Ollama, pull a model, start the server
ollama pull llama3.1
ollama serve                       # OpenAI-compatible API on :11434

# 2. Point Bitter at it (backend/.env)
ADVISOR_BACKEND=local
LOCAL_MODEL=llama3.1
LOCAL_MODEL_BASE_URL=http://localhost:11434/v1

# 3. Verify + run
python scripts/check_local.py
python scripts/run_real.py <owner/name>   # prints "Advisor backend used: local"
```

No key, no cost. Detection, correlation, and severity work exactly as with
Claude — only the advice wording comes from the local model.

## Testing

```bash
cd backend
pip install pytest
python -m pytest tests/ -q
```

CI runs the backend tests plus a frontend type-check and production build on
every push and pull request.

## Roadmap (post-MVP)

- GitHub App (fine-grained permissions, higher rate limits) in place of user OAuth
- Alembic migrations (MVP uses `create_all` on startup)
- More ecosystems (Go modules, Ruby, Gradle) via the detector registry
- Richer OSV/CVE severity scoring and per-repo digests
- Slack delivery channel
- Auto-fix PRs beyond version bumps (e.g. pyproject.toml, BOM-managed Maven deps)

## Status

MVP. The full pipeline (detection → correlation → advisor → report → delivery)
is implemented and verified end-to-end offline via `scripts/seed_demo.py`.

## License

[MIT](LICENSE)
