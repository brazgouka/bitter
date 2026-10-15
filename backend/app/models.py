"""SQLAlchemy ORM models — the platform's persistence layer.

Registered on Base.metadata by app.db.init_db(). All datetimes are
timezone-aware UTC; Report.period_date is a 'YYYY-MM-DD' string.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    login: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    access_token_enc: Mapped[str] = mapped_column(Text)
    # Encrypted GitLab personal access token, set via PUT /api/integrations/gitlab.
    # Separate from access_token_enc (the GitHub login token) since a user's
    # identity stays anchored to GitHub — GitLab is an optional extra source.
    gitlab_token_enc: Mapped[str | None] = mapped_column(Text)
    email_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mutes: Mapped[list["Mute"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "external_id", name="uq_repo_user_provider_external"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # "github" | "gitlab"
    provider: Mapped[str] = mapped_column(String(16), default="github")
    external_id: Mapped[int] = mapped_column(Integer, index=True)
    full_name: Mapped[str] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    monitored: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="repositories")
    components: Mapped[list["StackComponent"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class StackComponent(Base):
    """One detected technology/dependency in one repository."""

    __tablename__ = "stack_components"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "ecosystem",
            "name",
            "source_file",
            name="uq_component_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id"), index=True
    )
    # pypi / npm / docker / kubernetes / terraform / github-actions
    ecosystem: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    source_file: Mapped[str] = mapped_column(String(512))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    repository: Mapped["Repository"] = relationship(back_populates="components")


class EcosystemEvent(Base):
    """A normalized change in the wider ecosystem (advisory/release/deprecation)."""

    __tablename__ = "ecosystem_events"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", "technology", name="uq_event_identity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))  # osv / github-releases / endoflife
    ecosystem: Mapped[str] = mapped_column(String(64))
    technology: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(String(32))  # advisory/release/deprecation/breaking
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024))
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024))
    severity_hint: Mapped[str | None] = mapped_column(String(32))  # e.g. CRITICAL/HIGH
    affected_range: Mapped[str | None] = mapped_column(String(255))  # semver/PEP440 range
    introduced_version: Mapped[str | None] = mapped_column(String(128))
    fixed_version: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("user_id", "period_date", name="uq_report_user_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    status: Mapped[str] = mapped_column(String(32), default="generated")
    summary: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="reports")
    items: Mapped[list["ReportItem"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportItem.id",
    )


class ReportItem(Base):
    __tablename__ = "report_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("ecosystem_events.id"))
    severity: Mapped[str] = mapped_column(String(16))  # critical / medium / ignore
    technology: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024))
    body: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(512), index=True)
    # Fixability metadata (mirrors Finding), used by app.services.fixer to
    # locate and edit the right dependency line for a "propose fix" PR.
    tech_key: Mapped[str | None] = mapped_column(String(255))
    ecosystem: Mapped[str | None] = mapped_column(String(64))
    source_file: Mapped[str | None] = mapped_column(String(512))
    current_version: Mapped[str | None] = mapped_column(String(128))
    fixed_version: Mapped[str | None] = mapped_column(String(128))
    fix_pr_url: Mapped[str | None] = mapped_column(String(1024))
    # Every (source_file, current_version) pair this item's fix should bump —
    # e.g. the same package pinned in requirements.txt AND requirements-dev.txt
    # — so "Propose fix" always opens exactly one PR for the group.
    fix_targets: Mapped[list] = mapped_column(JSON, default=list)
    # The individual findings folded into this item (e.g. two CVEs plus a
    # newer release, all for the same package) — powers a readable "why"
    # list in the UI and PR body instead of one opaque blob of text.
    fix_reasons: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    report: Mapped["Report"] = relationship(back_populates="items")
    repository: Mapped["Repository | None"] = relationship()

    @property
    def repository_full_name(self) -> str | None:
        return self.repository.full_name if self.repository else None


class Mute(Base):
    __tablename__ = "mutes"
    __table_args__ = (
        UniqueConstraint("user_id", "technology", name="uq_mute_user_tech"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    technology: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="mutes")
