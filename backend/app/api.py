"""HTTP API: auth, repositories, reports, settings, and the run-now trigger.

Mounted under /api by app.main. Authentication is a signed session cookie set
by the GitHub OAuth callback (or the dev-login shortcut in development).
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import Mute, Report, ReportItem, Repository, User
from app.pipeline import run_pipeline, scan_one, sync_repositories
from app.schemas import (
    ComponentOut,
    GitLabConnect,
    MonitorUpdate,
    MuteCreate,
    MuteOut,
    ReasonOut,
    RepositoryDetailOut,
    RepositoryOut,
    ReportItemOut,
    ReportOut,
    ReportSummaryOut,
    RunResult,
    SettingsUpdate,
    UserOut,
)
from app.security import decrypt_token, encrypt_token
from app.services import fixer
from app.services import report as report_service
from app.services.github import GitHubClient, exchange_code_for_token
from app.services.gitlab import GitLabClient

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _user_out(user: User) -> UserOut:
    base = UserOut.model_validate(user).model_dump(exclude={"gitlab_connected"})
    return UserOut(**base, gitlab_connected=bool(user.gitlab_token_enc))


def _repo_out(repo: Repository) -> RepositoryOut:
    return RepositoryOut(
        id=repo.id,
        provider=repo.provider,
        full_name=repo.full_name,
        default_branch=repo.default_branch,
        private=repo.private,
        monitored=repo.monitored,
        last_scanned_at=repo.last_scanned_at,
        component_count=len(repo.components),
    )


def _report_item_out(item: ReportItem) -> ReportItemOut:
    affected_files = sorted(
        {t.get("source_file") for t in (item.fix_targets or []) if t.get("source_file")}
    )
    reasons = [
        ReasonOut(
            title=r.get("title") or "",
            detail=r.get("detail") or "",
            url=r.get("url"),
            fixed_version=r.get("fixed_version"),
        )
        for r in (item.fix_reasons or [])
    ]
    return ReportItemOut(
        id=item.id,
        severity=item.severity,
        technology=item.technology,
        title=item.title,
        body=item.body,
        action=item.action,
        repository_id=item.repository_id,
        repository_full_name=item.repository_full_name,
        event_id=item.event_id,
        fixed_version=item.fixed_version,
        fix_pr_url=item.fix_pr_url,
        can_propose_fix=fixer.can_auto_fix(item),
        reasons=reasons,
        affected_files=affected_files,
    )


def _report_out(report: Report) -> ReportOut:
    return ReportOut(
        id=report.id,
        period_date=report.period_date,
        generated_at=report.generated_at,
        status=report.status,
        summary=report.summary,
        items=[_report_item_out(it) for it in report.items],
    )


def _report_summary(report: Report) -> ReportSummaryOut:
    c = report_service.counts(report)
    return ReportSummaryOut(
        id=report.id,
        period_date=report.period_date,
        generated_at=report.generated_at,
        status=report.status,
        summary=report.summary,
        critical_count=c.get("critical", 0),
        medium_count=c.get("medium", 0),
        ignore_count=c.get("ignore", 0),
    )


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

@router.get("/auth/login")
def login(request: Request):
    if not settings.github_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub OAuth is not configured. Use /api/auth/dev-login in development.",
        )
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_callback_url,
            "scope": settings.github_oauth_scopes,
            "state": state,
        }
    )
    return RedirectResponse(f"{settings.github_oauth_base}/authorize?{query}")


@router.get("/auth/callback")
def auth_callback(request: Request, db: DbSession, code: str, state: str = ""):
    if state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    request.session.pop("oauth_state", None)

    token = exchange_code_for_token(code)
    with GitHubClient(token) as gh:
        profile = gh.get_user()
        email = profile.get("email") or gh.get_primary_email()

    user = db.query(User).filter(User.github_id == profile["id"]).first()
    if user is None:
        user = User(github_id=profile["id"], login=profile["login"])
        db.add(user)
    user.login = profile["login"]
    user.name = profile.get("name")
    user.email = email
    user.avatar_url = profile.get("avatar_url")
    user.access_token_enc = encrypt_token(token)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return RedirectResponse(f"{settings.frontend_url}/dashboard")


@router.post("/auth/dev-login", response_model=UserOut)
def dev_login(request: Request, db: DbSession):
    """Create/log in a demo user without GitHub. Development only."""
    if settings.environment != "development":
        raise HTTPException(status_code=404, detail="Not found")
    user = db.query(User).filter(User.github_id == 0).first()
    if user is None:
        user = User(
            github_id=0,
            login="demo",
            name="Demo Engineer",
            email="demo@bitter.dev",
            access_token_enc=encrypt_token("dev-no-token"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = user.id
    return _user_out(user)


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser):
    return _user_out(user)


# --------------------------------------------------------------------------
# Integrations (secondary Git providers)
# --------------------------------------------------------------------------

@router.put("/integrations/gitlab", response_model=UserOut)
def connect_gitlab(payload: GitLabConnect, user: CurrentUser, db: DbSession):
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Token is required")
    try:
        with GitLabClient(token) as gl:
            gl.get_user()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not authenticate with GitLab: {exc}")
    user.gitlab_token_enc = encrypt_token(token)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/integrations/gitlab", response_model=UserOut)
def disconnect_gitlab(user: CurrentUser, db: DbSession):
    user.gitlab_token_enc = None
    db.commit()
    db.refresh(user)
    return _user_out(user)


# --------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------

@router.get("/repos", response_model=list[RepositoryOut])
def list_repos(user: CurrentUser, db: DbSession):
    repos = (
        db.query(Repository)
        .filter(Repository.user_id == user.id)
        .order_by(Repository.full_name)
        .all()
    )
    return [_repo_out(r) for r in repos]


@router.post("/repos/sync", response_model=list[RepositoryOut])
def sync_repos(user: CurrentUser, db: DbSession):
    try:
        repos = sync_repositories(db, user)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Repository sync failed: {exc}")
    return [_repo_out(r) for r in repos]


@router.get("/repos/{repo_id}", response_model=RepositoryDetailOut)
def get_repo(repo_id: int, user: CurrentUser, db: DbSession):
    repo = db.get(Repository, repo_id)
    if repo is None or repo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Repository not found")
    base = _repo_out(repo)
    return RepositoryDetailOut(
        **base.model_dump(),
        components=[ComponentOut.model_validate(c) for c in repo.components],
    )


@router.patch("/repos/{repo_id}", response_model=RepositoryDetailOut)
def update_repo(repo_id: int, payload: MonitorUpdate, user: CurrentUser, db: DbSession):
    repo = db.get(Repository, repo_id)
    if repo is None or repo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Repository not found")
    repo.monitored = payload.monitored
    db.commit()
    if repo.monitored:  # scan immediately so the stack shows up
        try:
            scan_one(db, user, repo)
        except Exception:
            pass
    db.refresh(repo)
    base = _repo_out(repo)
    return RepositoryDetailOut(
        **base.model_dump(),
        components=[ComponentOut.model_validate(c) for c in repo.components],
    )


@router.post("/repos/{repo_id}/scan", response_model=RepositoryDetailOut)
def scan_repo(repo_id: int, user: CurrentUser, db: DbSession):
    repo = db.get(Repository, repo_id)
    if repo is None or repo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Repository not found")
    try:
        scan_one(db, user, repo)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")
    db.refresh(repo)
    base = _repo_out(repo)
    return RepositoryDetailOut(
        **base.model_dump(),
        components=[ComponentOut.model_validate(c) for c in repo.components],
    )


# --------------------------------------------------------------------------
# Reports + run
# --------------------------------------------------------------------------

@router.post("/run", response_model=RunResult)
def run_now(user: CurrentUser, db: DbSession):
    report = run_pipeline(db, user, do_scan=True, do_ingest=True, send_email=True)
    c = report_service.counts(report)
    return RunResult(
        report_id=report.id,
        period_date=report.period_date,
        critical_count=c.get("critical", 0),
        medium_count=c.get("medium", 0),
        ignore_count=c.get("ignore", 0),
        message="Pipeline complete.",
    )


@router.get("/reports", response_model=list[ReportSummaryOut])
def list_reports(user: CurrentUser, db: DbSession):
    reports = (
        db.query(Report)
        .filter(Report.user_id == user.id)
        .order_by(Report.period_date.desc())
        .all()
    )
    return [_report_summary(r) for r in reports]


@router.get("/reports/latest", response_model=ReportOut)
def latest_report(user: CurrentUser, db: DbSession):
    report = (
        db.query(Report)
        .filter(Report.user_id == user.id)
        .order_by(Report.period_date.desc())
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="No reports yet")
    return _report_out(report)


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: int, user: CurrentUser, db: DbSession):
    report = db.get(Report, report_id)
    if report is None or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_out(report)


@router.post("/reports/items/{item_id}/fix", response_model=ReportItemOut)
def propose_fix(item_id: int, user: CurrentUser, db: DbSession):
    item = db.get(ReportItem, item_id)
    if item is None or item.report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report item not found")
    try:
        fixer.propose_fix(db, user, item)
    except fixer.FixNotSupported as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not open fix PR: {exc}")
    return _report_item_out(item)


# --------------------------------------------------------------------------
# Settings + mutes
# --------------------------------------------------------------------------

@router.put("/settings", response_model=UserOut)
def update_settings(payload: SettingsUpdate, user: CurrentUser, db: DbSession):
    user.email_opt_in = payload.email_opt_in
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/mutes", response_model=list[MuteOut])
def list_mutes(user: CurrentUser, db: DbSession):
    return [MuteOut.model_validate(m) for m in user.mutes]


@router.post("/mutes", response_model=MuteOut)
def create_mute(payload: MuteCreate, user: CurrentUser, db: DbSession):
    existing = (
        db.query(Mute)
        .filter(Mute.user_id == user.id, Mute.technology == payload.technology)
        .first()
    )
    if existing:
        return MuteOut.model_validate(existing)
    mute = Mute(user_id=user.id, technology=payload.technology, reason=payload.reason)
    db.add(mute)
    db.commit()
    db.refresh(mute)
    return MuteOut.model_validate(mute)


@router.delete("/mutes/{mute_id}")
def delete_mute(mute_id: int, user: CurrentUser, db: DbSession):
    mute = db.get(Mute, mute_id)
    if mute is None or mute.user_id != user.id:
        raise HTTPException(status_code=404, detail="Mute not found")
    db.delete(mute)
    db.commit()
    return {"status": "ok"}
