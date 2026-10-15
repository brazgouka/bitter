"""Unit tests for app.services.versioning."""
from __future__ import annotations

from app.services import versioning


def test_max_version_picks_highest():
    assert versioning.max_version(["2.19.0", "2.31.0", "2.25.1"]) == "2.31.0"


def test_max_version_ignores_none_and_empty():
    assert versioning.max_version([None, "", "1.2.3"]) == "1.2.3"


def test_max_version_all_none_returns_none():
    assert versioning.max_version([None, None]) is None


def test_max_version_falls_back_to_first_unparsable():
    # Kubernetes apiVersion strings aren't semver — nothing parses, so we
    # still return *something* rather than silently dropping the proposal.
    assert versioning.max_version(["networking.k8s.io/v1"]) == "networking.k8s.io/v1"


def test_max_version_prefers_parsable_over_unparsable():
    assert versioning.max_version(["not-a-version", "1.0.0"]) == "1.0.0"
