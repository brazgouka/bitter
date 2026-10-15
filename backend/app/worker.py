"""APScheduler daily job that runs the pipeline for every user."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.pipeline import run_pipeline

log = logging.getLogger("bitter.worker")
_scheduler: BackgroundScheduler | None = None


def run_daily_job() -> None:
    db = SessionLocal()
    try:
        for user in db.query(User).all():
            try:
                run_pipeline(db, user, do_scan=True, do_ingest=True, send_email=True)
            except Exception as exc:
                log.warning("daily pipeline failed for %s: %s", user.login, exc)
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if not settings.enable_scheduler or _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_daily_job,
        CronTrigger(hour=settings.daily_run_hour, minute=settings.daily_run_minute),
        id="daily_digest",
        replace_existing=True,
    )
    _scheduler.start()
    log.info(
        "Scheduler started: daily digest at %02d:%02d UTC",
        settings.daily_run_hour,
        settings.daily_run_minute,
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
