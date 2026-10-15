"""Seed a demo user + repo + stack + synthetic ecosystem events, then run the
pipeline offline so you can see an end-to-end report without GitHub/Claude/network.

Run from the backend directory:  python scripts/seed_demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import EcosystemEvent, Repository, StackComponent, User  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402
from app.security import encrypt_token  # noqa: E402
from app.services import report as report_service  # noqa: E402

DEMO_COMPONENTS = [
    ("pypi", "requests", "==2.19.0", "requirements.txt"),
    ("pypi", "fastapi", "==0.68.0", "requirements.txt"),
    ("npm", "next", "^12.0.0", "package.json"),
    ("docker", "postgres", "13", "Dockerfile"),
    ("kubernetes", "networking.k8s.io/v1beta1 Ingress", None, "k8s/ingress.yaml"),
]

DEMO_EVENTS = [
    dict(
        source="osv", ecosystem="pypi", technology="requests", type="advisory",
        external_id="GHSA-demo-requests", title="requests leaks Proxy-Authorization header on redirect",
        summary="A demo advisory: affected versions may leak the Proxy-Authorization header across redirects.",
        url="https://osv.dev/vulnerability/GHSA-demo-requests", severity_hint="HIGH",
        introduced_version=None, fixed_version="2.31.0", raw={"aliases": ["CVE-DEMO-0001"]},
    ),
    dict(
        source="github-releases", ecosystem="npm", technology="next", type="release",
        external_id="vercel/next.js@v14.2.0", title="Next.js v14.2.0 released",
        summary="New major line with breaking changes since v12.",
        url="https://github.com/vercel/next.js/releases", raw={"version": "14.2.0", "tag": "v14.2.0"},
    ),
    dict(
        source="github-releases", ecosystem="pypi", technology="fastapi", type="release",
        external_id="fastapi/fastapi@0.115.0", title="FastAPI 0.115.0 released",
        summary="Numerous improvements since 0.68.", url="https://github.com/fastapi/fastapi/releases",
        raw={"version": "0.115.0", "tag": "0.115.0"},
    ),
    dict(
        source="endoflife", ecosystem="docker", technology="postgres", type="deprecation",
        external_id="postgresql:13", title="PostgreSQL 13 is end-of-life",
        url="https://endoflife.date/postgresql", affected_range="13",
        fixed_version="16", raw={"cycle": "13", "eol": True, "latest": "16"},
    ),
]


def _upsert_event(db, fields):
    exists = (
        db.query(EcosystemEvent)
        .filter_by(source=fields["source"], external_id=fields["external_id"], technology=fields["technology"])
        .first()
    )
    if not exists:
        db.add(EcosystemEvent(**fields))


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.github_id == 0).first()
        if user is None:
            user = User(github_id=0, login="demo", name="Demo Engineer",
                        email="demo@bitter.dev", access_token_enc=encrypt_token("dev-no-token"))
            db.add(user)
            db.commit()
            db.refresh(user)

        repo = (
            db.query(Repository)
            .filter(Repository.user_id == user.id, Repository.full_name == "demo-org/payments-api")
            .first()
        )
        if repo is None:
            repo = Repository(user_id=user.id, provider="github", external_id=100001,
                              full_name="demo-org/payments-api", default_branch="main",
                              private=True, monitored=True)
            db.add(repo)
            db.commit()
            db.refresh(repo)
        repo.monitored = True

        db.query(StackComponent).filter(StackComponent.repository_id == repo.id).delete()
        for eco, name, version, src in DEMO_COMPONENTS:
            db.add(StackComponent(repository_id=repo.id, ecosystem=eco, name=name,
                                  version=version, source_file=src))
        for fields in DEMO_EVENTS:
            _upsert_event(db, fields)
        db.commit()

        report = run_pipeline(db, user, do_scan=False, do_ingest=False, send_email=True)
        c = report_service.counts(report)
        print("\n=== DEMO REPORT ===")
        print(f"Report #{report.id} for {report.period_date} — status={report.status}")
        print(f"Summary: {report.summary}")
        print(f"Counts: critical={c['critical']} medium={c['medium']} ignore={c['ignore']}")
        for it in report.items:
            print(f"  [{it.severity.upper():8}] {it.technology}: {it.title}")
            if it.action:
                print(f"             action: {it.action}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
