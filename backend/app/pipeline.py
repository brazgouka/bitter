"""End-to-end orchestration: scan -> ingest -> correlate -> advise -> report.

Called by the /api/run endpoint (manual trigger) and the daily scheduler job.
Every stage is defensive: a failure in one repo or one data source degrades the
run rather than aborting it.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import Report, Repository, StackComponent, User, utcnow
from app.security import decrypt_token
from app.services import advisor, correlation, ingestion
from app.services import detection
from app.services import email as email_service
from app.services import providers
from app.services import report as report_service
from app.services.providers import ProviderNotConnected

log = logging.getLogger("bitter.pipeline")


def _sync_provider(db: Session, user: User, provider: str, client) -> None:
    remote = client.list_repositories()
    existing = {
        (r.provider, r.external_id): r
        for r in user.repositories
        if r.provider == provider
    }
    for gr in remote:
        r = existing.get((provider, gr.id))
        if r:
            r.full_name, r.default_branch, r.private = (
                gr.full_name,
                gr.default_branch,
                gr.private,
            )
        else:
            db.add(
                Repository(
                    user_id=user.id,
                    provider=provider,
                    external_id=gr.id,
                    full_name=gr.full_name,
                    default_branch=gr.default_branch,
                    private=gr.private,
                    monitored=False,
                )
            )


def sync_repositories(db: Session, user: User) -> list[Repository]:
    """Pull the user's repo list from every connected provider and upsert
    metadata (no scan)."""
    for provider in providers.PROVIDERS:
        try:
            with providers.client_for(user, provider) as client:
                _sync_provider(db, user, provider, client)
        except ProviderNotConnected:
            continue
    db.commit()
    return (
        db.query(Repository)
        .filter(Repository.user_id == user.id)
        .order_by(Repository.full_name)
        .all()
    )


def scan_repository(db: Session, gh: GitHubClient, repo: Repository) -> int:
    """Fingerprint one repo, replacing its stored components."""
    paths = gh.list_paths(repo.full_name, repo.default_branch)
    if not paths:  # empty repo or transient API failure — don't wipe existing data
        return len(repo.components)
    wanted = detection.relevant_paths(paths)
    files: dict[str, str] = {}
    for p in wanted:
        content = gh.get_file(repo.full_name, p, repo.default_branch)
        if content is not None:
            files[p] = content
    components = detection.detect_stack(files)

    db.query(StackComponent).filter(
        StackComponent.repository_id == repo.id
    ).delete()
    for c in components:
        db.add(
            StackComponent(
                repository_id=repo.id,
                ecosystem=c.ecosystem,
                name=c.name,
                version=c.version,
                source_file=c.source_file,
            )
        )
    repo.last_scanned_at = utcnow()
    db.commit()
    return len(components)


def scan_one(db: Session, user: User, repo: Repository) -> int:
    with providers.client_for(user, repo.provider) as client:
        return scan_repository(db, client, repo)


def scan_user(db: Session, user: User) -> int:
    total = 0
    repos = (
        db.query(Repository)
        .filter(Repository.user_id == user.id, Repository.monitored.is_(True))
        .all()
    )
    by_provider: dict[str, list[Repository]] = {}
    for repo in repos:
        by_provider.setdefault(repo.provider, []).append(repo)
    for provider, provider_repos in by_provider.items():
        try:
            client = providers.client_for(user, provider)
        except ProviderNotConnected as exc:
            log.warning("skipping %s repos: %s", provider, exc)
            continue
        with client:
            for repo in provider_repos:
                try:
                    total += scan_repository(db, client, repo)
                except Exception as exc:
                    log.warning("scan failed for %s: %s", repo.full_name, exc)
    return total


def _monitored_components(db: Session, user: User) -> list[detection.DetectedComponent]:
    rows = (
        db.query(StackComponent)
        .join(Repository)
        .filter(Repository.user_id == user.id, Repository.monitored.is_(True))
        .all()
    )
    return [
        detection.DetectedComponent(r.ecosystem, r.name, r.version, r.source_file)
        for r in rows
    ]


def run_pipeline(
    db: Session,
    user: User,
    *,
    do_scan: bool = True,
    do_ingest: bool = True,
    send_email: bool = True,
    period_date: str | None = None,
) -> Report:
    period_date = period_date or date.today().isoformat()

    if do_scan and (user.access_token_enc or user.gitlab_token_enc):
        try:
            scan_user(db, user)
        except Exception as exc:
            log.warning("scan_user failed for %s: %s", user.login, exc)

    components = _monitored_components(db, user)

    if do_ingest:
        token = None
        try:
            token = decrypt_token(user.access_token_enc)
        except Exception:
            token = None
        try:
            stats = ingestion.ingest_all(db, components, token)
            log.info("ingested %s", stats)
        except Exception as exc:
            log.warning("ingestion failed: %s", exc)

    findings = correlation.correlate(db, user)
    advice = advisor.assess(findings)
    report = report_service.build_report(db, user, advice, period_date)

    if send_email:
        sent = email_service.send_digest(user, report)
        if sent:
            report.status = "emailed"
            db.commit()
    return report
