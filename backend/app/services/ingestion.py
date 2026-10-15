"""Ecosystem change ingestion.

Pulls normalized EcosystemEvent rows from three public sources:
  * OSV.dev            -> security advisories for detected pypi/npm packages
  * GitHub Releases    -> new versions of tracked technologies
  * endoflife.date     -> deprecation / end-of-life cycles

Every network call is best-effort: failures are swallowed with a count so the
pipeline still completes if a source is unreachable (e.g. offline dev).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app import tech_registry
from app.models import EcosystemEvent
from app.services import versioning
from app.services.detection import DetectedComponent

log = logging.getLogger("bitter.ingestion")

_OSV_ECOSYSTEM = {"pypi": "PyPI", "npm": "npm", "maven": "Maven"}


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-ish timestamp/date, always returning tz-aware UTC.

    Sources mix full timestamps ("2024-06-01T00:00:00Z") and bare dates
    ("2024-06-01"); bare dates parse as naive, which crashes any subtraction
    against datetime.now(timezone.utc) (see _eol_within) unless normalized.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _upsert_event(db: Session, **fields) -> bool:
    existing = (
        db.query(EcosystemEvent)
        .filter_by(
            source=fields["source"],
            external_id=fields["external_id"],
            technology=fields["technology"],
        )
        .first()
    )
    if existing:
        return False
    db.add(EcosystemEvent(**fields))
    return True


# --------------------------------------------------------------------------
# OSV.dev — security advisories
# --------------------------------------------------------------------------

def _osv_severity(vuln: dict) -> str | None:
    ds = vuln.get("database_specific") or {}
    sev = ds.get("severity")
    if sev:
        return str(sev).upper()
    if vuln.get("severity"):
        return "CVSS"
    return None


def _osv_affected(vuln: dict, name: str) -> tuple[str | None, str | None]:
    introduced: str | None = None
    fixed: str | None = None
    for aff in vuln.get("affected", []) or []:
        pkg = aff.get("package", {}) or {}
        if pkg.get("name", "").lower() != name.lower():
            continue
        for rng in aff.get("ranges", []) or []:
            for ev in rng.get("events", []) or []:
                if ev.get("introduced") and ev["introduced"] != "0":
                    introduced = ev["introduced"]
                if ev.get("fixed"):
                    fixed = ev["fixed"]
    return introduced, fixed


def ingest_osv(db: Session, components: list[DetectedComponent], cap: int = 60) -> int:
    seen: set[tuple[str, str]] = set()
    added = 0
    for comp in components:
        eco = _OSV_ECOSYSTEM.get(comp.ecosystem)
        if not eco or (eco, comp.name) in seen:
            continue
        if eco == "Maven" and ":" not in comp.name:
            continue  # synthetic framework marker (e.g. "spring-boot"), not a real coordinate
        seen.add((eco, comp.name))
        if len(seen) > cap:
            break
        body: dict = {"package": {"ecosystem": eco, "name": comp.name}}
        parsed = versioning.parse(comp.version)
        if parsed is not None:
            body["version"] = str(parsed)
        try:
            r = httpx.post("https://api.osv.dev/v1/query", json=body, timeout=20)
            if r.status_code != 200:
                continue
            vulns = r.json().get("vulns", []) or []
        except httpx.HTTPError as exc:
            log.warning("OSV query failed for %s: %s", comp.name, exc)
            continue
        for vuln in vulns:
            vid = vuln.get("id")
            if not vid:
                continue
            introduced, fixed = _osv_affected(vuln, comp.name)
            summary = vuln.get("summary") or (vuln.get("details") or "")[:280]
            if _upsert_event(
                db,
                source="osv",
                ecosystem=comp.ecosystem,
                technology=comp.name,
                type="advisory",
                external_id=vid,
                title=summary or f"Security advisory {vid}",
                summary=(vuln.get("details") or "")[:2000] or None,
                url=f"https://osv.dev/vulnerability/{vid}",
                severity_hint=_osv_severity(vuln),
                affected_range=None,
                introduced_version=introduced,
                fixed_version=fixed,
                published_at=_parse_dt(vuln.get("published")),
                raw={"aliases": vuln.get("aliases", [])},
            ):
                added += 1
    db.commit()
    return added


# --------------------------------------------------------------------------
# GitHub Releases — new versions
# --------------------------------------------------------------------------

def ingest_github_releases(db: Session, token: str | None = None, per_tech: int = 5) -> int:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    added = 0
    for tech in tech_registry.github_tracked():
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{tech.github_repo}/releases",
                params={"per_page": per_tech},
                headers=headers,
                timeout=20,
            )
            if r.status_code != 200:
                continue
            releases = r.json()
        except httpx.HTTPError as exc:
            log.warning("GitHub releases failed for %s: %s", tech.github_repo, exc)
            continue
        for rel in releases:
            if rel.get("draft") or rel.get("prerelease"):
                continue
            tag = rel.get("tag_name")
            if not tag:
                continue
            version = versioning.parse(tag)
            if _upsert_event(
                db,
                source="github-releases",
                ecosystem=tech.ecosystem,
                technology=tech.key,
                type="release",
                external_id=f"{tech.github_repo}@{tag}",
                title=f"{tech.display_name} {tag} released",
                summary=(rel.get("body") or "")[:2000] or None,
                url=rel.get("html_url"),
                severity_hint=None,
                affected_range=None,
                introduced_version=None,
                fixed_version=None,
                published_at=_parse_dt(rel.get("published_at")),
                raw={"version": str(version) if version else tag, "tag": tag},
            ):
                added += 1
    db.commit()
    return added


# --------------------------------------------------------------------------
# endoflife.date — deprecation / EOL
# --------------------------------------------------------------------------

def _eol_within(eol, days: int = 240) -> bool:
    """True if the cycle is already EOL or reaches EOL within `days`."""
    if eol is True:
        return True
    if eol is False or eol is None:
        return False
    dt = _parse_dt(str(eol)) or _parse_dt(f"{eol}T00:00:00")
    if dt is None:
        return False
    delta = dt - datetime.now(timezone.utc)
    return delta.days <= days


def ingest_endoflife(db: Session) -> int:
    added = 0
    for tech in tech_registry.endoflife_tracked():
        try:
            r = httpx.get(
                f"https://endoflife.date/api/{tech.endoflife_product}.json", timeout=20
            )
            if r.status_code != 200:
                continue
            cycles = r.json()
        except httpx.HTTPError as exc:
            log.warning("endoflife failed for %s: %s", tech.endoflife_product, exc)
            continue
        for c in cycles:
            cycle = str(c.get("cycle"))
            eol = c.get("eol")
            if not _eol_within(eol):
                continue
            when = "is end-of-life" if eol is True else f"reaches end-of-life on {eol}"
            if _upsert_event(
                db,
                source="endoflife",
                ecosystem=tech.ecosystem,
                technology=tech.key,
                type="deprecation",
                external_id=f"{tech.endoflife_product}:{cycle}",
                title=f"{tech.display_name} {cycle} {when}",
                summary=None,
                url=f"https://endoflife.date/{tech.endoflife_product}",
                severity_hint=None,
                affected_range=cycle,
                introduced_version=None,
                fixed_version=str(c.get("latest")) if c.get("latest") else None,
                published_at=None,
                raw={"cycle": cycle, "eol": eol, "latest": c.get("latest")},
            ):
                added += 1
    db.commit()
    return added


def ingest_all(
    db: Session, components: list[DetectedComponent], token: str | None = None
) -> dict[str, int]:
    return {
        "osv": ingest_osv(db, components),
        "releases": ingest_github_releases(db, token),
        "endoflife": ingest_endoflife(db),
    }
