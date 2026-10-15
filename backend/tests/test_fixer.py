"""Unit tests for the pure version-bump functions in app.services.fixer."""
from __future__ import annotations

import pytest

from app.services import fixer


def test_bump_requirements_pinned_line():
    content = "fastapi==0.115.0\nrequests==2.19.0  # pinned\ndjango\n"
    out = fixer._bump_requirements(content, "requests", "2.31.0")
    assert "requests==2.31.0  # pinned\n" in out
    assert "fastapi==0.115.0\n" in out  # untouched
    assert "django\n" in out  # untouched


def test_bump_requirements_case_insensitive_name():
    content = "Requests==2.19.0\n"
    out = fixer._bump_requirements(content, "requests", "2.31.0")
    assert out == "Requests==2.31.0\n"


def test_bump_requirements_no_pin_raises():
    content = "requests>=2.0\n"
    with pytest.raises(fixer.FixNotSupported):
        fixer._bump_requirements(content, "requests", "2.31.0")


def test_bump_package_json_caret_preserved():
    content = '{\n  "dependencies": {\n    "next": "^12.0.0",\n    "lodash": "4.17.15"\n  }\n}\n'
    out = fixer._bump_package_json(content, "next", "14.2.0")
    assert '"next": "^14.2.0"' in out
    assert '"lodash": "4.17.15"' in out  # untouched


def test_bump_package_json_exact_no_prefix():
    content = '{"dependencies": {"lodash": "4.17.15"}}'
    out = fixer._bump_package_json(content, "lodash", "4.17.21")
    assert '"lodash": "4.17.21"' in out


def test_bump_package_json_non_semver_untouched_raises():
    content = '{"dependencies": {"mylib": "workspace:*"}}'
    with pytest.raises(fixer.FixNotSupported):
        fixer._bump_package_json(content, "mylib", "1.0.0")


def test_bump_pom_parent_spring_boot():
    content = (
        "<project>\n"
        "  <parent>\n"
        "    <groupId>org.springframework.boot</groupId>\n"
        "    <artifactId>spring-boot-starter-parent</artifactId>\n"
        "    <version>2.6.3</version>\n"
        "  </parent>\n"
        "</project>\n"
    )
    out = fixer._bump_pom_xml(content, "spring-boot", "4.1.0")
    assert "<version>4.1.0</version>" in out
    assert "2.6.3" not in out


def test_bump_pom_dependency_explicit_version():
    content = (
        "<dependencies>\n"
        "  <dependency>\n"
        "    <groupId>com.google.guava</groupId>\n"
        "    <artifactId>guava</artifactId>\n"
        "    <version>31.0.1-jre</version>\n"
        "  </dependency>\n"
        "</dependencies>\n"
    )
    out = fixer._bump_pom_xml(content, "com.google.guava:guava", "32.0.0-android")
    assert "<version>32.0.0-android</version>" in out
    assert "31.0.1-jre" not in out


def test_bump_pom_dependency_via_property():
    content = (
        "<properties>\n  <guava.version>31.0.1-jre</guava.version>\n</properties>\n"
        "<dependencies>\n"
        "  <dependency>\n"
        "    <groupId>com.google.guava</groupId>\n"
        "    <artifactId>guava</artifactId>\n"
        "    <version>${guava.version}</version>\n"
        "  </dependency>\n"
        "</dependencies>\n"
    )
    out = fixer._bump_pom_xml(content, "com.google.guava:guava", "32.0.0-android")
    assert "<guava.version>32.0.0-android</guava.version>" in out
    assert "${guava.version}" in out  # the reference itself is untouched


def test_bump_pom_dependency_bom_managed_raises():
    content = (
        "<dependencies>\n"
        "  <dependency>\n"
        "    <groupId>org.springframework.boot</groupId>\n"
        "    <artifactId>spring-boot-starter-web</artifactId>\n"
        "  </dependency>\n"
        "</dependencies>\n"
    )
    with pytest.raises(fixer.FixNotSupported):
        fixer._bump_pom_xml(content, "org.springframework.boot:spring-boot-starter-web", "3.0.0")


def test_bump_k8s_api_version_targets_right_document():
    content = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: unrelated\n"
        "---\n"
        "apiVersion: networking.k8s.io/v1beta1\n"
        "kind: Ingress\n"
        "metadata:\n  name: web\n"
    )
    out = fixer._bump_k8s_api_version(
        content, "networking.k8s.io/v1beta1 Ingress", "networking.k8s.io/v1"
    )
    assert "apiVersion: apps/v1" in out  # untouched
    assert "apiVersion: networking.k8s.io/v1\nkind: Ingress" in out
    assert "networking.k8s.io/v1beta1" not in out


def test_can_auto_fix_gating():
    class FakeItem:
        fix_pr_url = None
        fixed_version = "2.31.0"
        source_file = "requirements.txt"
        tech_key = "requests"
        ecosystem = "pypi"

    assert fixer.can_auto_fix(FakeItem()) is True

    f = FakeItem()
    f.fix_pr_url = "https://github.com/x/y/pull/1"
    assert fixer.can_auto_fix(f) is False  # already proposed

    f2 = FakeItem()
    f2.source_file = "pyproject.toml"
    assert fixer.can_auto_fix(f2) is False  # unsupported file

    f3 = FakeItem()
    f3.fixed_version = None
    assert fixer.can_auto_fix(f3) is False  # no target version


# --------------------------------------------------------------------------
# Multi-target grouping support
# --------------------------------------------------------------------------

def test_targets_for_prefers_fix_targets_list():
    class FakeItem:
        fix_targets = [
            {"source_file": "requirements.txt", "current_version": "2.19.0"},
            {"source_file": "requirements-dev.txt", "current_version": "2.20.0"},
        ]
        source_file = "requirements.txt"
        current_version = "2.19.0"

    out = fixer._targets_for(FakeItem())
    assert out == FakeItem.fix_targets


def test_targets_for_falls_back_to_scalar_fields():
    class FakeItem:
        fix_targets = []
        source_file = "requirements.txt"
        current_version = "2.19.0"

    out = fixer._targets_for(FakeItem())
    assert out == [{"source_file": "requirements.txt", "current_version": "2.19.0"}]


def test_targets_for_drops_entries_without_source_file():
    class FakeItem:
        fix_targets = [{"source_file": None, "current_version": "1.0.0"}]
        source_file = None
        current_version = None

    assert fixer._targets_for(FakeItem()) == []


# --------------------------------------------------------------------------
# Code-usage scanning helpers
# --------------------------------------------------------------------------

def test_candidate_source_paths_filters_by_ecosystem():
    paths = ["app/main.py", "app/utils.py", "README.md", "web/index.ts", "web/app.tsx"]
    assert fixer._candidate_source_paths(paths, "pypi") == ["app/main.py", "app/utils.py"]
    assert fixer._candidate_source_paths(paths, "npm") == ["web/index.ts", "web/app.tsx"]
    assert fixer._candidate_source_paths(paths, "maven") == []  # not scanned


def test_usage_pattern_matches_python_imports():
    pattern = fixer._usage_pattern("pypi", "requests")
    assert pattern.search("import requests\n")
    assert pattern.search("from requests import Session\n")
    assert pattern.search("from requests.exceptions import Timeout\n")
    assert not pattern.search("import requests_toolbelt\n")


def test_usage_pattern_matches_npm_imports():
    pattern = fixer._usage_pattern("npm", "axios")
    assert pattern.search("import axios from 'axios';")
    assert pattern.search("const axios = require('axios');")
    assert pattern.search("import client from \"axios/lib/core\";")
    assert not pattern.search("import axiosRetry from 'axios-retry';")
