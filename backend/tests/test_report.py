"""Unit tests for the pure grouping/merge logic in app.services.report."""
from __future__ import annotations

from app.services.advisor import ScoredFinding
from app.services.correlation import Finding
from app.services.report import _group_key, _merge_group


def _finding(**overrides) -> Finding:
    base = dict(
        repository_id=1,
        repository_full_name="acme/widgets",
        technology="Requests",
        tech_key="requests",
        ecosystem="pypi",
        kind="advisory",
        current_version="2.19.0",
        title="CVE-1",
        detail="detail",
        url="https://example.com/cve-1",
        event_id=10,
        severity_hint="HIGH",
        fixed_version="2.25.0",
        dedup_key="1:advisory:cve-1",
        source_file="requirements.txt",
    )
    base.update(overrides)
    return Finding(**base)


def _scored(finding: Finding, severity="medium", action="do it") -> ScoredFinding:
    return ScoredFinding(finding=finding, severity=severity, title=finding.title, body=finding.detail, action=action)


def test_group_key_groups_by_repo_ecosystem_tech():
    a = _scored(_finding())
    b = _scored(_finding(event_id=11, dedup_key="1:advisory:cve-2"))
    assert _group_key(a) == _group_key(b)


def test_merge_group_single_item_passes_through_unchanged():
    sf = _scored(_finding(), severity="critical", action="Upgrade now")
    merged = _merge_group([sf])
    assert merged["title"] == sf.title
    assert merged["body"] == sf.body
    assert merged["action"] == "Upgrade now"
    assert merged["severity"] == "critical"
    assert merged["fixed_version"] == "2.25.0"


def test_merge_group_multi_item_picks_highest_version_and_worst_severity():
    cve1 = _scored(
        _finding(title="CVE-1", fixed_version="2.25.0", dedup_key="1:advisory:cve-1"),
        severity="medium",
    )
    cve2 = _scored(
        _finding(title="CVE-2", fixed_version="2.31.0", dedup_key="1:advisory:cve-2", event_id=11),
        severity="critical",
    )
    release = _scored(
        _finding(
            title="New release",
            kind="release",
            fixed_version="2.28.0",
            dedup_key="1:release:requests:2.28.0",
            event_id=12,
        ),
        severity="ignore",
    )
    merged = _merge_group([cve1, cve2, release])

    assert merged["severity"] == "critical"  # worst of medium/critical/ignore
    assert merged["fixed_version"] == "2.31.0"  # highest of 2.25.0/2.31.0/2.28.0
    assert "CVE-1" in merged["body"]
    assert "CVE-2" in merged["body"]
    assert "New release" in merged["body"]
    assert len(merged["fix_reasons"]) == 3
    assert merged["dedup_key"] == "1:pypi:requests"


def test_merge_group_dedup_key_stable_across_ecosystem_and_tech():
    npm_finding = _scored(
        _finding(
            repository_id=2,
            ecosystem="npm",
            tech_key="axios",
            technology="Axios",
            dedup_key="2:advisory:axios-cve",
        )
    )
    merged = _merge_group([npm_finding])
    assert merged["dedup_key"] == "2:npm:axios"
