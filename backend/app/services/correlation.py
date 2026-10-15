"""Correlation engine: intersect the user's stack with ecosystem events.

Produces `Finding` candidates (still un-scored) from four matchers:
  * OSV advisories affecting a detected package at its version
  * Newer releases of a tracked technology the user is behind on
  * endoflife.date cycles the user's version falls into
  * Kubernetes removed-API usage (curated static table)

Findings are handed to the advisor, which assigns severity + an action.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import tech_registry
from app.models import EcosystemEvent, Mute, Repository, StackComponent, User
from app.services import versioning


@dataclass
class Finding:
    repository_id: int
    repository_full_name: str
    technology: str  # display name
    tech_key: str  # normalized key
    ecosystem: str
    kind: str  # advisory / release / deprecation / breaking
    current_version: str | None
    title: str
    detail: str
    url: str | None
    event_id: int | None
    severity_hint: str | None
    fixed_version: str | None
    dedup_key: str
    source_file: str | None = None


# Curated map of removed Kubernetes APIs -> (removed_in_release, replacement).
# Keyed by "<apiVersion> <Kind>" to match StackComponent.name for k8s manifests.
K8S_REMOVED_APIS: dict[str, tuple[str, str]] = {
    "extensions/v1beta1 Ingress": ("v1.22", "networking.k8s.io/v1"),
    "networking.k8s.io/v1beta1 Ingress": ("v1.22", "networking.k8s.io/v1"),
    "extensions/v1beta1 Deployment": ("v1.16", "apps/v1"),
    "apps/v1beta1 Deployment": ("v1.16", "apps/v1"),
    "apps/v1beta2 Deployment": ("v1.16", "apps/v1"),
    "extensions/v1beta1 DaemonSet": ("v1.16", "apps/v1"),
    "extensions/v1beta1 ReplicaSet": ("v1.16", "apps/v1"),
    "policy/v1beta1 PodSecurityPolicy": ("v1.25", "Pod Security Admission"),
    "policy/v1beta1 PodDisruptionBudget": ("v1.25", "policy/v1"),
    "batch/v1beta1 CronJob": ("v1.25", "batch/v1"),
    "autoscaling/v2beta1 HorizontalPodAutoscaler": ("v1.25", "autoscaling/v2"),
    "autoscaling/v2beta2 HorizontalPodAutoscaler": ("v1.26", "autoscaling/v2"),
    "flowcontrol.apiserver.k8s.io/v1beta1 FlowSchema": ("v1.29", "flowcontrol.apiserver.k8s.io/v1"),
    "rbac.authorization.k8s.io/v1beta1 ClusterRole": ("v1.22", "rbac.authorization.k8s.io/v1"),
    "rbac.authorization.k8s.io/v1beta1 Role": ("v1.22", "rbac.authorization.k8s.io/v1"),
}


def _muted_keys(db: Session, user_id: int) -> set[str]:
    return {
        m.technology.strip().lower()
        for m in db.query(Mute).filter(Mute.user_id == user_id).all()
    }


def _is_muted(muted: set[str], *names: str | None) -> bool:
    return any(n and n.strip().lower() in muted for n in names)


def _events(db: Session, source: str, technology: str, type_: str) -> list[EcosystemEvent]:
    return (
        db.query(EcosystemEvent)
        .filter(
            EcosystemEvent.source == source,
            EcosystemEvent.technology == technology,
            EcosystemEvent.type == type_,
        )
        .all()
    )


def _advisory_findings(
    db: Session, repo: Repository, comp: StackComponent
) -> list[Finding]:
    out: list[Finding] = []
    for ev in _events(db, "osv", comp.name, "advisory"):
        # If the advisory declares a range, honour it; otherwise trust the
        # version-scoped OSV query that produced it.
        if ev.introduced_version or ev.fixed_version:
            if not versioning.in_osv_range(
                comp.version, ev.introduced_version, ev.fixed_version
            ):
                continue
        fix = f" Fixed in {ev.fixed_version}." if ev.fixed_version else ""
        out.append(
            Finding(
                repository_id=repo.id,
                repository_full_name=repo.full_name,
                technology=tech_registry.display_name(comp.name),
                tech_key=comp.name,
                ecosystem=comp.ecosystem,
                kind="advisory",
                current_version=comp.version,
                title=ev.title,
                detail=(ev.summary or ev.title) + fix,
                url=ev.url,
                event_id=ev.id,
                severity_hint=ev.severity_hint,
                fixed_version=ev.fixed_version,
                dedup_key=f"{repo.id}:advisory:{ev.external_id}",
                source_file=comp.source_file,
            )
        )
    return out


def _release_findings(
    db: Session, repo: Repository, comp: StackComponent, tech
) -> list[Finding]:
    out: list[Finding] = []
    best: EcosystemEvent | None = None
    best_v = comp.version
    for ev in _events(db, "github-releases", tech.key, "release"):
        rel_v = (ev.raw or {}).get("version")
        if rel_v and versioning.is_older(best_v, rel_v):
            best, best_v = ev, rel_v
    if best is None:
        return out
    rel_v = (best.raw or {}).get("version")
    breaking = versioning.is_major_bump(comp.version, rel_v)
    out.append(
        Finding(
            repository_id=repo.id,
            repository_full_name=repo.full_name,
            technology=tech.display_name,
            tech_key=tech.key,
            ecosystem=comp.ecosystem,
            kind="breaking" if breaking else "release",
            current_version=comp.version,
            title=best.title,
            detail=(
                f"You are on {comp.version or 'an unpinned version'}; "
                f"{tech.display_name} {rel_v} is available."
                + (" This is a new major version — expect breaking changes." if breaking else "")
            ),
            url=best.url,
            event_id=best.id,
            severity_hint=best.severity_hint,
            fixed_version=rel_v,
            dedup_key=f"{repo.id}:release:{tech.key}:{rel_v}",
            source_file=comp.source_file,
        )
    )
    return out


def _eol_findings(
    db: Session, repo: Repository, comp: StackComponent, tech
) -> list[Finding]:
    out: list[Finding] = []
    cur = versioning.parse(comp.version)
    for ev in _events(db, "endoflife", tech.key, "deprecation"):
        cycle = (ev.raw or {}).get("cycle")
        # Match when the detected version starts with the cycle line (e.g.
        # cycle "3.9" matches version "3.9.7") or when we have no version.
        matches = cur is None
        if cycle and cur is not None:
            matches = str(comp.version).lstrip("v=^~>< ").startswith(str(cycle))
        if not matches:
            continue
        out.append(
            Finding(
                repository_id=repo.id,
                repository_full_name=repo.full_name,
                technology=tech.display_name,
                tech_key=tech.key,
                ecosystem=comp.ecosystem,
                kind="deprecation",
                current_version=comp.version,
                title=ev.title,
                detail=ev.title
                + (f" Latest supported release is {ev.fixed_version}." if ev.fixed_version else ""),
                url=ev.url,
                event_id=ev.id,
                severity_hint=ev.severity_hint,
                fixed_version=ev.fixed_version,
                dedup_key=f"{repo.id}:eol:{ev.external_id}",
                source_file=comp.source_file,
            )
        )
    return out


def _k8s_findings(repo: Repository, comp: StackComponent) -> list[Finding]:
    removed = K8S_REMOVED_APIS.get(comp.name)
    if not removed:
        return []
    removed_in, replacement = removed
    return [
        Finding(
            repository_id=repo.id,
            repository_full_name=repo.full_name,
            technology="Kubernetes",
            tech_key="kubernetes",
            ecosystem="kubernetes",
            kind="deprecation",
            current_version=None,
            title=f"Kubernetes API {comp.name} is removed in {removed_in}",
            detail=(
                f"{repo.full_name} uses {comp.name} (in {comp.source_file}), which is "
                f"removed as of Kubernetes {removed_in}. Migrate to {replacement}."
            ),
            url="https://kubernetes.io/docs/reference/using-api/deprecation-guide/",
            event_id=None,
            severity_hint="CRITICAL",
            fixed_version=replacement,
            dedup_key=f"{repo.id}:k8s:{comp.name}",
            source_file=comp.source_file,
        )
    ]


def correlate(db: Session, user: User, per_repo_cap: int = 40) -> list[Finding]:
    muted = _muted_keys(db, user.id)
    findings: list[Finding] = []
    seen: set[str] = set()

    repos = (
        db.query(Repository)
        .filter(Repository.user_id == user.id, Repository.monitored.is_(True))
        .all()
    )
    for repo in repos:
        repo_findings: list[Finding] = []
        for comp in repo.components:
            tech = tech_registry.lookup(comp.name)
            if _is_muted(muted, comp.name, tech.key if tech else None):
                continue

            if comp.ecosystem in ("pypi", "npm", "maven"):
                repo_findings += _advisory_findings(db, repo, comp)
            if tech and tech.github_repo:
                repo_findings += _release_findings(db, repo, comp, tech)
            if tech and tech.endoflife_product:
                repo_findings += _eol_findings(db, repo, comp, tech)
            if comp.ecosystem == "kubernetes":
                repo_findings += _k8s_findings(repo, comp)

        for f in repo_findings[:per_repo_cap]:
            if f.dedup_key in seen:
                continue
            seen.add(f.dedup_key)
            findings.append(f)
    return findings
