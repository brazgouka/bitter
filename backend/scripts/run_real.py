"""Run the full Bitter pipeline against one or more REAL GitHub repositories,
using a real Claude advisor when ANTHROPIC_API_KEY is set.

Setup (backend/.env or environment):
  GITHUB_TOKEN=ghp_...           # public repos need no scope; private need 'repo'
  ANTHROPIC_API_KEY=sk-ant-...   # optional; without it the heuristic runs

Run from the backend directory:
  python scripts/run_real.py <owner/name> [<owner/name> ...]
  e.g. python scripts/run_real.py tiangolo/full-stack-fastapi-template
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Repository, StackComponent, User  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402
from app.security import encrypt_token  # noqa: E402
from app.services import advisor  # noqa: E402
from app.services import report as report_service  # noqa: E402
from app.services.github import GitHubClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(name)s: %(message)s")

SEV_ICON = {"critical": "🔴", "medium": "🟠", "ignore": "⚪"}


def _advisor_state() -> str:
    backend = (settings.advisor_backend or "auto").lower()
    if backend == "auto":
        backend = "claude" if settings.anthropic_api_key else "heuristic"
    if backend == "claude":
        return f"Claude ({settings.anthropic_model})"
    if backend == "local":
        return f"Local — {settings.local_model} @ {settings.local_model_base_url}"
    return "Heuristic (no LLM, free)"


def main() -> None:
    repos = [a for a in sys.argv[1:] if not a.startswith("-")]
    token = settings.github_token or os.environ.get("GITHUB_TOKEN")

    if not token:
        print("❌ Set GITHUB_TOKEN in backend/.env or the environment.")
        sys.exit(1)
    if not repos:
        print("usage: python scripts/run_real.py <owner/name> [<owner/name> ...]")
        sys.exit(1)

    print(f"\nAdvisor backend:   {_advisor_state()}")
    print(f"Repositories:      {', '.join(repos)}\n")

    init_db()
    db = SessionLocal()
    try:
        # Identify the token's owner and upsert a user carrying that token.
        with GitHubClient(token) as gh:
            profile = gh.get_user()
        user = db.query(User).filter(User.github_id == profile["id"]).first()
        if user is None:
            user = User(github_id=profile["id"], login=profile["login"])
            db.add(user)
        user.login = profile["login"]
        user.name = profile.get("name")
        user.email = profile.get("email")
        user.access_token_enc = encrypt_token(token)
        db.commit()
        db.refresh(user)
        print(f"Authenticated as: {user.login}\n")

        # Register each requested repo as monitored.
        with GitHubClient(token) as gh:
            for full in repos:
                try:
                    meta = gh.get_repo(full)
                except Exception as exc:
                    print(f"  ⚠️  Could not access {full}: {exc}")
                    continue
                r = (
                    db.query(Repository)
                    .filter_by(user_id=user.id, provider="github", external_id=meta.id)
                    .first()
                )
                if r is None:
                    r = Repository(
                        user_id=user.id,
                        provider="github",
                        external_id=meta.id,
                        full_name=meta.full_name,
                        default_branch=meta.default_branch,
                        private=meta.private,
                        monitored=True,
                    )
                    db.add(r)
                else:
                    r.full_name = meta.full_name
                    r.default_branch = meta.default_branch
                    r.private = meta.private
                    r.monitored = True
                db.commit()

        print("Running pipeline (scan → ingest → correlate → advise → report)…\n")
        report = run_pipeline(db, user, do_scan=True, do_ingest=True, send_email=False)

        # --- Detected stack ---
        for repo in (
            db.query(Repository)
            .filter(Repository.user_id == user.id, Repository.monitored.is_(True))
            .all()
        ):
            comps = (
                db.query(StackComponent)
                .filter(StackComponent.repository_id == repo.id)
                .all()
            )
            print(f"── {repo.full_name}: {len(comps)} components detected")
            by_eco: dict[str, int] = {}
            for c in comps:
                by_eco[c.ecosystem] = by_eco.get(c.ecosystem, 0) + 1
            for eco, n in sorted(by_eco.items()):
                print(f"     {eco}: {n}")

        # --- Report ---
        c = report_service.counts(report)
        print("\n=== DIGEST ===")
        print(f"Advisor backend used: {advisor.LAST_RUN_BACKEND}")
        print(f"Date: {report.period_date}   status={report.status}")
        print(f"Summary: {report.summary}")
        print(f"Counts: critical={c['critical']} medium={c['medium']} ignore={c['ignore']}\n")
        for it in sorted(report.items, key=lambda i: advisor.SEVERITY_ORDER.get(i.severity, 3)):
            print(f"{SEV_ICON.get(it.severity, '•')} [{it.severity.upper():8}] {it.technology}: {it.title}")
            print(f"     {it.body}")
            if it.action:
                print(f"     → {it.action}")
        if not report.items:
            print("(No findings — this stack has no matching advisories/releases/EOL today.)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
