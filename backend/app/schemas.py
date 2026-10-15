"""Pydantic request/response models for the HTTP API (app.api)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    email_opt_in: bool = True
    gitlab_connected: bool = False


class ComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ecosystem: str
    name: str
    version: str | None = None
    source_file: str


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    full_name: str
    default_branch: str
    private: bool
    monitored: bool
    last_scanned_at: datetime | None = None
    component_count: int = 0


class RepositoryDetailOut(RepositoryOut):
    components: list[ComponentOut] = []


class ReasonOut(BaseModel):
    title: str
    detail: str
    url: str | None = None
    fixed_version: str | None = None


class ReportItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    severity: str
    technology: str
    title: str
    body: str
    action: str | None = None
    repository_id: int | None = None
    repository_full_name: str | None = None
    event_id: int | None = None
    fixed_version: str | None = None
    fix_pr_url: str | None = None
    can_propose_fix: bool = False
    reasons: list[ReasonOut] = []
    affected_files: list[str] = []


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_date: str
    generated_at: datetime
    status: str
    summary: str | None = None
    items: list[ReportItemOut] = []


class ReportSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_date: str
    generated_at: datetime
    status: str
    summary: str | None = None
    critical_count: int = 0
    medium_count: int = 0
    ignore_count: int = 0


class MuteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    technology: str
    reason: str | None = None


class MonitorUpdate(BaseModel):
    monitored: bool


class MuteCreate(BaseModel):
    technology: str
    reason: str | None = None


class SettingsUpdate(BaseModel):
    email_opt_in: bool


class GitLabConnect(BaseModel):
    token: str


class RunResult(BaseModel):
    report_id: int
    period_date: str
    critical_count: int
    medium_count: int
    ignore_count: int
    message: str
