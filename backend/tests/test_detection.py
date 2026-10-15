"""Unit tests for the pure detection parsers."""
from __future__ import annotations

from app.services import detection, versioning


def _by(components, ecosystem, name):
    return next(
        (c for c in components if c.ecosystem == ecosystem and c.name == name), None
    )


def test_requirements_txt():
    content = "fastapi==0.115.0\nrequests>=2.0  # comment\n-r other.txt\ndjango\n"
    comps = detection._parse_requirements(content, "requirements.txt")
    fastapi = _by(comps, "pypi", "fastapi")
    assert fastapi and fastapi.version == "==0.115.0"
    assert _by(comps, "pypi", "requests").version == ">=2.0"
    assert _by(comps, "pypi", "django").version is None
    assert _by(comps, "pypi", "other.txt") is None  # -r line ignored


def test_package_json():
    content = '{"dependencies":{"react":"^18.2.0"},"devDependencies":{"typescript":"5.4.0"}}'
    comps = detection._parse_package_json(content, "package.json")
    assert _by(comps, "npm", "react").version == "^18.2.0"
    assert _by(comps, "npm", "typescript").version == "5.4.0"


def test_dockerfile():
    content = "FROM python:3.11-slim AS base\nRUN echo hi\nFROM postgres:15\n"
    comps = detection._parse_dockerfile(content, "Dockerfile")
    # Full tag is kept (e.g. "3.11-slim"); versioning.parse() normalises it for
    # comparison, so the extra variant suffix is harmless and more informative.
    assert _by(comps, "docker", "python").version == "3.11-slim"
    assert _by(comps, "docker", "postgres").version == "15"


def test_kubernetes_apiversion():
    content = "apiVersion: networking.k8s.io/v1beta1\nkind: Ingress\nmetadata:\n  name: web\n"
    comps = detection._parse_k8s(content, "k8s/ingress.yaml")
    assert _by(comps, "kubernetes", "networking.k8s.io/v1beta1 Ingress") is not None


def test_maven_pom_xml():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>2.6.3</version>
  </parent>
  <groupId>com.helper</groupId>
  <artifactId>advisor</artifactId>
  <version>0.0.1-SNAPSHOT</version>
  <properties>
    <guava.version>31.0.1-jre</guava.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>${guava.version}</version>
    </dependency>
    <dependency>
      <groupId>org.postgresql</groupId>
      <artifactId>postgresql</artifactId>
      <scope>runtime</scope>
    </dependency>
  </dependencies>
</project>"""
    comps = detection._parse_pom_xml(content, "pom.xml")
    # Spring Boot version is sourced from <parent>, not an explicit dependency.
    assert _by(comps, "maven", "spring-boot").version == "2.6.3"
    # Deps with no explicit <version> (managed by the parent BOM) still register.
    assert _by(comps, "maven", "org.springframework.boot:spring-boot-starter-web") is not None
    assert (
        _by(comps, "maven", "org.springframework.boot:spring-boot-starter-web").version
        is None
    )
    # ${property} placeholders resolve against <properties>.
    assert _by(comps, "maven", "com.google.guava:guava").version == "31.0.1-jre"
    # Deps with neither explicit version nor property still register, version None.
    assert _by(comps, "maven", "org.postgresql:postgresql") is not None


def test_maven_pom_xml_malformed():
    assert detection._parse_pom_xml("<project><oops", "pom.xml") == []


def test_terraform_provider():
    content = 'provider "aws" {}\nterraform {\n  required_version = ">= 1.5.0"\n}\n'
    comps = detection._parse_terraform(content, "main.tf")
    assert _by(comps, "terraform", "aws") is not None
    assert _by(comps, "terraform", "terraform").version == ">= 1.5.0"


def test_github_actions():
    content = "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"
    comps = detection._parse_actions(content, ".github/workflows/ci.yml")
    assert _by(comps, "github-actions", "actions/checkout").version == "v4"


def test_relevant_paths_filters():
    paths = [
        "requirements.txt",
        "src/app.py",
        "Dockerfile",
        ".github/workflows/ci.yml",
        "infra/main.tf",
        "k8s/deploy.yaml",
        "docs/notes.yaml",  # not a k8s-ish path
        "README.md",
        "pom.xml",
    ]
    picked = set(detection.relevant_paths(paths))
    assert "requirements.txt" in picked
    assert "Dockerfile" in picked
    assert ".github/workflows/ci.yml" in picked
    assert "infra/main.tf" in picked
    assert "k8s/deploy.yaml" in picked
    assert "pom.xml" in picked
    assert "src/app.py" not in picked
    assert "README.md" not in picked


def test_versioning():
    assert versioning.is_older("1.0.0", "2.0.0")
    assert not versioning.is_older("2.0.0", "1.0.0")
    assert versioning.is_major_bump("^12.0.0", "14.2.0")
    assert not versioning.is_major_bump("14.1.0", "14.2.0")
    assert versioning.in_osv_range("2.19.0", None, "2.31.0")
    assert not versioning.in_osv_range("2.31.0", None, "2.31.0")


def test_detect_stack_dedupes():
    files = {
        "requirements.txt": "fastapi==0.115.0\n",
        "sub/requirements.txt": "fastapi==0.115.0\n",
    }
    comps = detection.detect_stack(files)
    # same name, different source_file -> two distinct records
    assert len([c for c in comps if c.name == "fastapi"]) == 2
