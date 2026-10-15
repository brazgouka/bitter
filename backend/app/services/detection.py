"""Stack fingerprinting: turn repository files into detected components.

`relevant_paths` picks which files are worth fetching from a repo's tree;
`detect_stack` parses their contents into DetectedComponent records. All
parsers are pure (no I/O), so they are directly unit-testable.
"""
from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedComponent:
    ecosystem: str  # pypi/npm/docker/kubernetes/terraform/github-actions/maven
    name: str
    version: str | None
    source_file: str


# --------------------------------------------------------------------------
# Path selection
# --------------------------------------------------------------------------

_K8S_HINTS = ("k8s", "kube", "manifest", "deploy", "helm", "chart", "/templates/")


def _looks_like_k8s_path(path: str) -> bool:
    low = path.lower()
    if "/" not in low:  # a yaml at repo root
        return True
    return any(hint in low for hint in _K8S_HINTS)


def relevant_paths(paths: list[str], cap: int = 60) -> list[str]:
    """Subset of a repo's blob paths worth fetching for fingerprinting."""
    picked: list[str] = []
    for p in paths:
        base = p.rsplit("/", 1)[-1].lower()
        if base in ("requirements.txt", "pyproject.toml", "package.json") or (
            base.startswith("requirements") and base.endswith(".txt")
        ):
            picked.append(p)
        elif base == "dockerfile" or base.startswith("dockerfile."):
            picked.append(p)
        elif base == "pom.xml":
            picked.append(p)
        elif base.endswith(".tf"):
            picked.append(p)
        elif p.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
            picked.append(p)
        elif base.endswith((".yml", ".yaml")) and _looks_like_k8s_path(p):
            picked.append(p)
        if len(picked) >= cap:
            break
    return picked


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
    r"(==|===|~=|>=|<=|!=|<|>)?\s*([A-Za-z0-9][A-Za-z0-9._*+!-]*)?"
)


def _parse_requirements(content: str, path: str) -> list[DetectedComponent]:
    out: list[DetectedComponent] = []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http", ".")):
            continue
        m = _REQ_RE.match(line)
        if not m:
            continue
        name = m.group(1).lower()
        op, num = m.group(2), m.group(3)
        spec = f"{op}{num}" if op and num else (num or None)
        out.append(DetectedComponent("pypi", name, spec, path))
    return out


def _pep508_name(dep: str) -> tuple[str, str | None]:
    m = _REQ_RE.match(dep.strip())
    if not m:
        return dep.strip().lower(), None
    op, num = m.group(2), m.group(3)
    spec = f"{op}{num}" if op and num else (num or None)
    return m.group(1).lower(), spec


def _parse_pyproject(content: str, path: str) -> list[DetectedComponent]:
    out: list[DetectedComponent] = []
    try:
        data = tomllib.loads(content)
    except Exception:
        return out
    project = data.get("project", {}) or {}
    for dep in project.get("dependencies", []) or []:
        name, spec = _pep508_name(dep)
        out.append(DetectedComponent("pypi", name, spec, path))
    for group in (project.get("optional-dependencies") or {}).values():
        for dep in group or []:
            name, spec = _pep508_name(dep)
            out.append(DetectedComponent("pypi", name, spec, path))
    poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
    for name, spec in (poetry.get("dependencies") or {}).items():
        if name.lower() == "python":
            continue
        version = spec if isinstance(spec, str) else (
            spec.get("version") if isinstance(spec, dict) else None
        )
        out.append(DetectedComponent("pypi", name.lower(), version, path))
    return out


def _parse_package_json(content: str, path: str) -> list[DetectedComponent]:
    out: list[DetectedComponent] = []
    try:
        data = json.loads(content)
    except Exception:
        return out
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        for name, spec in (data.get(key) or {}).items():
            out.append(
                DetectedComponent(
                    "npm", name.lower(), spec if isinstance(spec, str) else None, path
                )
            )
    return out


_FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE)


def _parse_dockerfile(content: str, path: str) -> list[DetectedComponent]:
    out: list[DetectedComponent] = []
    for line in content.splitlines():
        m = _FROM_RE.match(line)
        if not m:
            continue
        image = m.group(1)
        if image.lower() == "scratch" or image.startswith("$"):
            continue
        name_part, _, tag = image.partition(":")
        base = name_part.split("/")[-1].lower()
        tag = tag.split("@")[0] or None  # drop digest pin
        out.append(DetectedComponent("docker", base, tag, path))
    return out


def _xml_tag(elem: ET.Element) -> str:
    tag = elem.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xml_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _xml_tag(child) == name:
            return child
    return None


def _xml_text(elem: ET.Element, name: str) -> str | None:
    child = _xml_child(elem, name)
    return child.text.strip() if child is not None and child.text else None


def _parse_pom_xml(content: str, path: str) -> list[DetectedComponent]:
    """Maven pom.xml: dependencies as 'groupId:artifactId' (matches OSV's Maven
    package-name format), plus a synthetic 'spring-boot' component sourced
    from <parent> when the project inherits spring-boot-starter-parent (most
    Spring Boot deps carry no explicit <version> — it comes from that BOM).
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    out: list[DetectedComponent] = []

    parent = _xml_child(root, "parent")
    if parent is not None:
        parent_artifact = _xml_text(parent, "artifactId") or ""
        parent_version = _xml_text(parent, "version")
        if parent_version and "spring-boot" in parent_artifact:
            out.append(DetectedComponent("maven", "spring-boot", parent_version, path))

    props: dict[str, str] = {}
    props_elem = _xml_child(root, "properties")
    if props_elem is not None:
        for child in props_elem:
            if child.text:
                props[_xml_tag(child)] = child.text.strip()

    def resolve(v: str | None) -> str | None:
        if v and v.startswith("${") and v.endswith("}"):
            return props.get(v[2:-1], v)
        return v

    deps = _xml_child(root, "dependencies")
    if deps is not None:
        for dep in deps:
            if _xml_tag(dep) != "dependency":
                continue
            group = _xml_text(dep, "groupId")
            artifact = _xml_text(dep, "artifactId")
            if not group or not artifact:
                continue
            version = resolve(_xml_text(dep, "version"))
            out.append(DetectedComponent("maven", f"{group}:{artifact}", version, path))

    return out


_AV_RE = re.compile(r"^\s*apiVersion:\s*['\"]?([\w./-]+)['\"]?", re.MULTILINE)
_KIND_RE = re.compile(r"^\s*kind:\s*['\"]?([\w.-]+)['\"]?", re.MULTILINE)


def _parse_k8s(content: str, path: str) -> list[DetectedComponent]:
    if "apiVersion:" not in content or "kind:" not in content:
        return []
    out: list[DetectedComponent] = []
    for doc in re.split(r"^---\s*$", content, flags=re.MULTILINE):
        av = _AV_RE.search(doc)
        kd = _KIND_RE.search(doc)
        if av and kd:
            out.append(
                DetectedComponent("kubernetes", f"{av.group(1)} {kd.group(1)}", None, path)
            )
    return out


_PROVIDER_RE = re.compile(r'provider\s+"([\w-]+)"')
_REQUIRED_PROVIDER_RE = re.compile(
    r'source\s*=\s*"([^"]+)"(?:[^}]*?version\s*=\s*"([^"]+)")?', re.DOTALL
)
_REQUIRED_VERSION_RE = re.compile(r'required_version\s*=\s*"([^"]+)"')


def _parse_terraform(content: str, path: str) -> list[DetectedComponent]:
    out: list[DetectedComponent] = []
    for m in _PROVIDER_RE.finditer(content):
        out.append(DetectedComponent("terraform", m.group(1).lower(), None, path))
    for m in _REQUIRED_PROVIDER_RE.finditer(content):
        name = m.group(1).split("/")[-1].lower()
        out.append(DetectedComponent("terraform", name, m.group(2), path))
    m = _REQUIRED_VERSION_RE.search(content)
    if m:
        out.append(DetectedComponent("terraform", "terraform", m.group(1), path))
    return out


_USES_RE = re.compile(r"uses:\s*['\"]?([\w.\-]+/[\w.\-]+)@([\w.\-]+)")


def _parse_actions(content: str, path: str) -> list[DetectedComponent]:
    return [
        DetectedComponent("github-actions", m.group(1).lower(), m.group(2), path)
        for m in _USES_RE.finditer(content)
    ]


def _detect_file(path: str, content: str) -> list[DetectedComponent]:
    base = path.rsplit("/", 1)[-1].lower()
    if base == "requirements.txt" or (
        base.startswith("requirements") and base.endswith(".txt")
    ):
        return _parse_requirements(content, path)
    if base == "pyproject.toml":
        return _parse_pyproject(content, path)
    if base == "package.json":
        return _parse_package_json(content, path)
    if base == "dockerfile" or base.startswith("dockerfile."):
        return _parse_dockerfile(content, path)
    if base == "pom.xml":
        return _parse_pom_xml(content, path)
    if path.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
        return _parse_actions(content, path)
    if base.endswith(".tf"):
        return _parse_terraform(content, path)
    if base.endswith((".yml", ".yaml")):
        return _parse_k8s(content, path)
    return []


def detect_stack(files: dict[str, str]) -> list[DetectedComponent]:
    """Run every parser over {path: content}; de-duplicate identical records."""
    seen: set[tuple[str, str, str]] = set()
    result: list[DetectedComponent] = []
    for path, content in files.items():
        for comp in _detect_file(path, content):
            key = (comp.ecosystem, comp.name, comp.source_file)
            if key in seen:
                continue
            seen.add(key)
            result.append(comp)
    return result
