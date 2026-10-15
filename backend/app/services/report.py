"""Assemble and persist the daily Report from scored advice.

Findings for the same (repository, ecosystem, technology) are folded into a
single ReportItem — e.g. two separate CVEs against the same package, or an
advisory plus a newer release — instead of one card per finding. The merged
item proposes the single highest fixed_version among the group and lists
every underlying reason, so "Propose fix" opens exactly one PR that bumps
every affected file to one version, rather than several redundant (and
possibly conflicting) PRs.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Report, ReportItem, StackComponent, User, utcnow
from app.services import versioning
from app.services.advisor import SEVERITY_ORDER, Advice, ScoredFinding


def counts(report: Report) -> dict[str, int]:
    out = {"critical": 0, "medium": 0, "ignore": 0}
    for it in report.items:
        out[it.severity] = out.get(it.severity, 0) + 1
    return out


def _group_key(sf: ScoredFinding) -> tuple:
    f = sf.finding
    return (f.repository_id, f.ecosystem, f.tech_key)


def _merge_group(group: list[ScoredFinding]) -> dict:
    """Pure merge of one (repo, ecosystem, tech_key) group into ReportItem
    field values. No DB access here — `fix_targets` is filled in by the
    caller, which needs a Session to look up every affected source file.
    """
    primary = group[0].finding
    worst = min(group, key=lambda s: SEVERITY_ORDER.get(s.severity, 3))
    fixed_version = versioning.max_version([g.finding.fixed_version for g in group])
    reasons = [
        {
            "title": g.title,
            "detail": g.body,
            "url": g.finding.url,
            "kind": g.finding.kind,
            "fixed_version": g.finding.fixed_version,
        }
        for g in group
    ]

    if len(group) == 1:
        # The common case: pass the advisor's own text through untouched.
        title, body, action = group[0].title, group[0].body, group[0].action
    else:
        title = (
            f"Upgrade {primary.technology} to {fixed_version}"
            if fixed_version
            else f"{len(group)} issues affect {primary.technology}"
        )
        body = (
            f"{len(group)} issues affect {primary.technology} in "
            f"{primary.repository_full_name}, folded into one proposed fix: "
            + "; ".join(r["title"] for r in reasons)
            + "."
        )
        action = (
            f"Upgrade {primary.technology} to {fixed_version} or later — "
            "this addresses every issue listed above."
            if fixed_version
            else group[0].action
        )

    return dict(
        repository_id=primary.repository_id,
        event_id=primary.event_id,
        severity=worst.severity,
        technology=primary.technology,
        title=title,
        body=body,
        action=action,
        dedup_key=f"{primary.repository_id}:{primary.ecosystem}:{primary.tech_key}",
        tech_key=primary.tech_key,
        ecosystem=primary.ecosystem,
        source_file=primary.source_file,
        current_version=primary.current_version,
        fixed_version=fixed_version,
        fix_reasons=reasons,
    )


def _fix_targets(
    db: Session,
    repository_id: int | None,
    ecosystem: str | None,
    tech_key: str | None,
    fallback_file: str | None,
    fallback_version: str | None,
) -> list[dict]:
    """Every (source_file, current_version) StackComponent matching this
    group's technology in this repo — covers a package pinned in more than
    one manifest (e.g. requirements.txt and requirements-dev.txt) so a single
    "Propose fix" bumps all of them together.
    """
    if repository_id is None or not ecosystem or not tech_key:
        return []
    rows = (
        db.query(StackComponent)
        .filter(
            StackComponent.repository_id == repository_id,
            StackComponent.ecosystem == ecosystem,
            StackComponent.name == tech_key,
        )
        .all()
    )
    if rows:
        return [{"source_file": r.source_file, "current_version": r.version} for r in rows]
    if fallback_file:
        return [{"source_file": fallback_file, "current_version": fallback_version}]
    return []


def build_report(db: Session, user: User, advice: Advice, period_date: str) -> Report:
    """Create or replace the user's report for `period_date` (YYYY-MM-DD)."""
    report = (
        db.query(Report)
        .filter(Report.user_id == user.id, Report.period_date == period_date)
        .first()
    )
    if report is None:
        report = Report(user_id=user.id, period_date=period_date, summary=advice.summary)
        db.add(report)
        db.flush()
    else:
        for it in list(report.items):
            db.delete(it)
        db.flush()
        report.summary = advice.summary
        report.generated_at = utcnow()
        report.status = "generated"

    groups: dict[tuple, list[ScoredFinding]] = {}
    order: list[tuple] = []
    for sf in advice.items:
        key = _group_key(sf)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(sf)

    merged = [_merge_group(groups[key]) for key in order]
    merged.sort(key=lambda m: SEVERITY_ORDER.get(m["severity"], 3))

    for kwargs in merged:
        kwargs["fix_targets"] = _fix_targets(
            db,
            kwargs["repository_id"],
            kwargs["ecosystem"],
            kwargs["tech_key"],
            kwargs["source_file"],
            kwargs["current_version"],
        )
        db.add(ReportItem(report_id=report.id, **kwargs))
    db.commit()
    db.refresh(report)
    return report
