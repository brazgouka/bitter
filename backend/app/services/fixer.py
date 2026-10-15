""""Propose fix" — turn one grouped finding into a real PR/MR.

Two layers:

  1. Manifest bump (deterministic, unit-tested) — same conservative scope as
     before: pypi requirements*.txt, npm package.json, maven pom.xml,
     kubernetes apiVersion. A grouped ReportItem may carry several targets
     (e.g. the same package pinned in requirements.txt AND
     requirements-dev.txt, or the same removed Kubernetes API used in
     several manifests) — every resolvable target is bumped to the group's
     single proposed version, in one branch/PR. A target that can't be
     bumped (e.g. pyproject.toml) is skipped and noted in the PR body rather
     than aborting the whole fix.

  2. Code migration (best-effort, LLM-backed) — for pypi/npm only, after the
     manifest bump the repo is scanned for source files that import the
     upgraded package. If the configured advisor backend is Claude or a
     local OpenAI-compatible model, it is asked to rewrite any usages the
     new version breaks (renamed/removed APIs, changed signatures, changed
     import paths, ...) and those files are committed to the same branch.
     This step is skipped — not failed — when no LLM backend is configured
     or no importing file is found; the manifest-only PR still opens either
     way. Maven and Kubernetes app code is not scanned: there's no reliable
     way to map a Maven coordinate or a Kubernetes apiVersion string back to
     source usages the way an import statement maps to a package name.

Not supported (raises FixNotSupported when NO target can be bumped):
pyproject.toml, unpinned/ranged specs, Maven deps whose version is inherited
from a parent BOM with no local property to bump.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ReportItem, User
from app.services import advisor as advisor_service
from app.services import providers

log = logging.getLogger("bitter.fixer")


class FixNotSupported(Exception):
    pass


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------

def can_auto_fix(item: ReportItem) -> bool:
    """Optimistic check for showing/hiding the 'Propose fix' button.

    The actual edit may still fail at apply-time (e.g. a Maven dep whose
    version can't be resolved) — that surfaces as a clear error, not a bug.
    """
    if item.fix_pr_url:
        return False  # already proposed
    if not (item.fixed_version and item.source_file and item.tech_key and item.ecosystem):
        return False
    if item.ecosystem == "pypi":
        base = item.source_file.rsplit("/", 1)[-1].lower()
        return base != "pyproject.toml"
    return item.ecosystem in ("npm", "maven", "kubernetes")


# --------------------------------------------------------------------------
# Ecosystem-specific bumpers (pure, unit-testable)
# --------------------------------------------------------------------------

_PIN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\s*\[[^\]]*\])?\s*==\s*"
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9._*+!-]*)"
)


def _bump_requirements(content: str, dep_name: str, new_version: str) -> str:
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        code = line.split("#", 1)[0]
        m = _PIN_RE.match(code)
        if not m or m.group("name").lower() != dep_name.lower():
            continue
        start, end = m.span("version")
        lines[i] = line[:start] + new_version + line[end:]
        return "".join(lines)
    raise FixNotSupported(f"No pinned '==' requirement found for {dep_name!r}")


def _bump_package_json(content: str, dep_name: str, new_version: str) -> str:
    pattern = re.compile(r'("' + re.escape(dep_name) + r'"\s*:\s*")([^"]*)(")')
    changed = False

    def repl(m: re.Match) -> str:
        nonlocal changed
        old = m.group(2)
        prefix_m = re.match(r"^([\^~>=<]*)\d", old)
        if not prefix_m:  # "latest", "workspace:*", a git url, ... — leave alone
            return m.group(0)
        changed = True
        prefix = re.match(r"^[\^~>=<]*", old).group(0)
        return f"{m.group(1)}{prefix}{new_version}{m.group(3)}"

    new_content = pattern.sub(repl, content)
    if not changed:
        raise FixNotSupported(f"No simple version spec found for {dep_name!r} in package.json")
    return new_content


def _bump_pom_parent(content: str, new_version: str) -> str:
    m = re.search(r"(<parent>.*?<version>)([^<]+)(</version>.*?</parent>)", content, re.DOTALL)
    if not m:
        raise FixNotSupported("No <parent><version> found in pom.xml")
    return content[: m.start(2)] + new_version + content[m.end(2) :]


def _bump_pom_dependency(content: str, group_artifact: str, new_version: str) -> str:
    group, _, artifact = group_artifact.partition(":")
    for m in re.finditer(r"<dependency>.*?</dependency>", content, re.DOTALL):
        block = m.group(0)
        if f"<groupId>{group}</groupId>" not in block:
            continue
        if f"<artifactId>{artifact}</artifactId>" not in block:
            continue
        ver_m = re.search(r"<version>([^<]+)</version>", block)
        if not ver_m:
            raise FixNotSupported(
                f"{group_artifact} has no explicit <version> — it's likely managed "
                "by a parent BOM and can't be safely auto-bumped."
            )
        old_ver = ver_m.group(1).strip()
        if old_ver.startswith("${") and old_ver.endswith("}"):
            prop = old_ver[2:-1]
            prop_re = re.compile(rf"(<{re.escape(prop)}>)([^<]*)(</{re.escape(prop)}>)")
            if not prop_re.search(content):
                raise FixNotSupported(f"Could not resolve property {old_ver} for {group_artifact}")
            return prop_re.sub(
                lambda pm: f"{pm.group(1)}{new_version}{pm.group(3)}", content, count=1
            )
        start, end = m.span()
        new_block = block[: ver_m.start(1)] + new_version + block[ver_m.end(1) :]
        return content[:start] + new_block + content[end:]
    raise FixNotSupported(f"Dependency {group_artifact} not found in pom.xml")


def _bump_pom_xml(content: str, tech_key: str, new_version: str) -> str:
    if tech_key == "spring-boot":
        return _bump_pom_parent(content, new_version)
    if ":" in tech_key:
        return _bump_pom_dependency(content, tech_key, new_version)
    raise FixNotSupported(f"Unrecognized Maven component key: {tech_key!r}")


def _bump_k8s_api_version(content: str, tech_key: str, new_api_version: str) -> str:
    old_av, _, kind = tech_key.partition(" ")
    if not old_av or not kind:
        raise FixNotSupported(f"Unrecognized Kubernetes component key: {tech_key!r}")
    # Split on document separators while keeping them, so the rejoin is exact.
    parts = re.split(r"(^---[ \t]*$)", content, flags=re.MULTILINE)
    av_re = re.compile(
        r"(^[ \t]*apiVersion:\s*['\"]?)" + re.escape(old_av) + r"(['\"]?[ \t]*$)",
        re.MULTILINE,
    )
    kind_re = re.compile(
        r"^[ \t]*kind:\s*['\"]?" + re.escape(kind) + r"['\"]?[ \t]*$", re.MULTILINE
    )
    for i, part in enumerate(parts):
        if av_re.search(part) and kind_re.search(part):
            parts[i] = av_re.sub(rf"\g<1>{new_api_version}\2", part, count=1)
            return "".join(parts)
    raise FixNotSupported(f"Could not locate {tech_key} in the manifest to bump")


def _bump_manifest(
    ecosystem: str, tech_key: str, fixed_version: str, source_file: str, old_content: str
) -> str:
    if ecosystem == "pypi":
        base = source_file.rsplit("/", 1)[-1].lower()
        if base == "pyproject.toml":
            raise FixNotSupported(
                "Automatic fix isn't supported for pyproject.toml yet — please update manually."
            )
        return _bump_requirements(old_content, tech_key, fixed_version)
    if ecosystem == "npm":
        return _bump_package_json(old_content, tech_key, fixed_version)
    if ecosystem == "maven":
        return _bump_pom_xml(old_content, tech_key, fixed_version)
    if ecosystem == "kubernetes":
        return _bump_k8s_api_version(old_content, tech_key, fixed_version)
    raise FixNotSupported(f"No automatic fix available for ecosystem {ecosystem!r}")


def _targets_for(item: ReportItem) -> list[dict]:
    targets = [t for t in (item.fix_targets or []) if t.get("source_file")]
    if targets:
        return targets
    if item.source_file:
        return [{"source_file": item.source_file, "current_version": item.current_version}]
    return []


# --------------------------------------------------------------------------
# Code-usage scanning (pure filters + best-effort LLM rewrite)
# --------------------------------------------------------------------------

_CODE_EXTENSIONS = {
    "pypi": (".py",),
    "npm": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
}
_MAX_CODE_FILES = 6
_MAX_CODE_BYTES = 30_000


def _candidate_source_paths(paths: list[str], ecosystem: str) -> list[str]:
    exts = _CODE_EXTENSIONS.get(ecosystem)
    if not exts:
        return []
    return [p for p in paths if p.lower().endswith(exts)]


def _usage_pattern(ecosystem: str, tech_key: str) -> re.Pattern | None:
    name = re.escape(tech_key)
    if ecosystem == "pypi":
        return re.compile(
            rf"^\s*(?:from\s+{name}(?:\.\S*)?\s+import\b|import\s+{name}\b)", re.MULTILINE
        )
    if ecosystem == "npm":
        return re.compile(rf"""(?:from\s+|require\()\s*['"]{name}(?:/[^'"]*)?['"]""")
    return None


def _find_usages(
    client, full_name: str, ref: str, paths: list[str], ecosystem: str, tech_key: str
) -> dict[str, str]:
    """{path: content} for every candidate source file that actually imports
    tech_key, capped so the follow-up LLM call stays small and fast.
    """
    pattern = _usage_pattern(ecosystem, tech_key)
    if pattern is None:
        return {}
    found: dict[str, str] = {}
    budget = _MAX_CODE_BYTES
    for p in _candidate_source_paths(paths, ecosystem):
        if len(found) >= _MAX_CODE_FILES or budget <= 0:
            break
        content = client.get_file(full_name, p, ref)
        if content is None or not pattern.search(content):
            continue
        found[p] = content
        budget -= len(content)
    return found


_CODE_FIX_SYSTEM = (
    "You are Bitter's automatic dependency-upgrade fixer. A library is being "
    "upgraded; you are given its release/advisory notes and every source file "
    "in the repository that imports it. For each file, decide whether the "
    "upgrade requires a code change there (a renamed or removed API, a "
    "changed function signature, a new required argument, a changed import "
    "path, a renamed config key, etc). Only include files that actually need "
    "a change — omit anything unaffected. Return the COMPLETE new contents "
    "of each included file, not a diff or partial snippet, and leave "
    "everything unrelated to the upgrade exactly as it was."
)

_CODE_FIX_TOOL = {
    "name": "submit_code_changes",
    "description": (
        "Return updated file contents for any source files that need code "
        "changes for this dependency upgrade."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "new_content": {"type": "string"},
                        "summary": {
                            "type": "string",
                            "description": "One short sentence describing what changed and why.",
                        },
                    },
                    "required": ["path", "new_content", "summary"],
                },
            }
        },
        "required": ["files"],
    },
}


def _code_fix_payload(
    technology: str,
    current_version: str | None,
    new_version: str | None,
    reasons: list[dict],
    usages: dict[str, str],
) -> str:
    payload = {
        "technology": technology,
        "current_version": current_version,
        "new_version": new_version,
        "release_notes": [
            {"title": r.get("title"), "detail": r.get("detail")} for r in reasons
        ],
        "files": [{"path": p, "content": c} for p, c in usages.items()],
    }
    return json.dumps(payload)


def _code_changes_claude(
    technology: str,
    current_version: str | None,
    new_version: str | None,
    reasons: list[dict],
    usages: dict[str, str],
) -> list[dict]:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=8192,
        system=_CODE_FIX_SYSTEM,
        tools=[_CODE_FIX_TOOL],
        tool_choice={"type": "tool", "name": "submit_code_changes"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Assess whether this upgrade requires code changes and return "
                    "them via submit_code_changes.\n\n"
                    + _code_fix_payload(technology, current_version, new_version, reasons, usages)
                ),
            }
        ],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_code_changes":
            return block.input.get("files") or []
    return []


_CODE_FIX_LOCAL_INSTRUCTIONS = (
    "Return ONLY a single JSON object (no markdown, no commentary) of the form:\n"
    '{"files": [{"path": "…", "new_content": "…", "summary": "…"}]}\n'
    "Omit any file that needs no change. Return complete file contents, not diffs."
)


def _code_changes_local(
    technology: str,
    current_version: str | None,
    new_version: str | None,
    reasons: list[dict],
    usages: dict[str, str],
) -> list[dict]:
    import httpx

    url = settings.local_model_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.local_model_api_key:
        headers["Authorization"] = f"Bearer {settings.local_model_api_key}"
    payload = {
        "model": settings.local_model,
        "messages": [
            {"role": "system", "content": _CODE_FIX_SYSTEM + "\n\n" + _CODE_FIX_LOCAL_INSTRUCTIONS},
            {
                "role": "user",
                "content": _code_fix_payload(technology, current_version, new_version, reasons, usages),
            },
        ],
        "temperature": 0.1,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=240)
    if resp.status_code == 400:  # some servers reject response_format
        payload.pop("response_format", None)
        resp = httpx.post(url, json=payload, headers=headers, timeout=240)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return advisor_service.extract_json(content).get("files") or []


def _propose_code_changes(
    technology: str,
    current_version: str | None,
    new_version: str | None,
    reasons: list[dict],
    usages: dict[str, str],
) -> list[dict]:
    """Ask the configured advisor backend to rewrite broken usages. Returns
    [] (never raises) when no LLM backend is configured, there's nothing to
    scan, or the call fails — the manifest bump still lands as its own PR.
    """
    if not usages:
        return []
    backend = advisor_service.resolve_backend()
    try:
        if backend == "claude":
            return _code_changes_claude(technology, current_version, new_version, reasons, usages)
        if backend == "local":
            return _code_changes_local(technology, current_version, new_version, reasons, usages)
    except Exception as exc:
        log.warning("code-migration scan failed for %s: %s", technology, exc)
    return []


def _apply_code_migration(client, repo, branch: str, item: ReportItem) -> list[tuple[str, str]]:
    paths = client.list_paths(repo.full_name, branch)
    usages = _find_usages(client, repo.full_name, branch, paths, item.ecosystem, item.tech_key)
    if not usages:
        return []
    proposals = _propose_code_changes(
        item.technology, item.current_version, item.fixed_version, item.fix_reasons or [], usages
    )
    applied: list[tuple[str, str]] = []
    for f in proposals:
        path, new_content = f.get("path"), f.get("new_content")
        if not path or new_content is None or path not in usages or new_content == usages[path]:
            continue
        fetched = client.get_file_with_sha(repo.full_name, path, branch)
        if fetched is None:
            continue
        _, sha = fetched
        client.update_file(
            repo.full_name,
            path,
            branch,
            new_content,
            f"Bitter: update {path} for {item.technology} {item.fixed_version}",
            sha,
        )
        applied.append((path, f.get("summary") or f"Updated for {item.technology} {item.fixed_version}."))
    return applied


# --------------------------------------------------------------------------
# PR/MR description
# --------------------------------------------------------------------------

def _build_pr_body(item: ReportItem, changed_files: list[tuple[str, str]], skipped: list[str]) -> str:
    parts = [item.body.strip(), ""]

    reasons = item.fix_reasons or []
    if len(reasons) > 1:
        parts.append("### Why")
        for r in reasons:
            line = f"- {r.get('title')}"
            if r.get("url"):
                line += f" ([details]({r['url']}))"
            parts.append(line)
        parts.append("")

    if item.action:
        parts.append(f"**Action:** {item.action}")
        parts.append("")

    if changed_files:
        parts.append("### Files changed")
        for path, summary in changed_files:
            parts.append(f"- `{path}` — {summary}")
        parts.append("")

    if skipped:
        parts.append("### Skipped (needs a manual look)")
        for s in skipped:
            parts.append(f"- {s}")
        parts.append("")

    parts.append("_Opened automatically by Bitter — the Engineering Intelligence Platform._")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# GitHub / GitLab flow
# --------------------------------------------------------------------------

def propose_fix(db: Session, user: User, item: ReportItem) -> str:
    """Bump every target, best-effort migrate affected code, open one PR/MR."""
    if item.fix_pr_url:
        return item.fix_pr_url
    if not can_auto_fix(item):
        raise FixNotSupported("This finding doesn't support an automatic fix.")

    repo = item.repository
    if repo is None:
        raise FixNotSupported("No repository associated with this finding.")

    targets = _targets_for(item)
    changed_files: list[tuple[str, str]] = []
    skipped: list[str] = []

    with providers.client_for(user, repo.provider) as client:
        branch = f"bitter-fix-{item.id}"
        base_ref_sha = client.get_ref_sha(repo.full_name, repo.default_branch)
        client.create_branch(repo.full_name, branch, base_ref_sha)

        for target in targets:
            source_file = target.get("source_file")
            fetched = client.get_file_with_sha(repo.full_name, source_file, branch) or (
                client.get_file_with_sha(repo.full_name, source_file, repo.default_branch)
            )
            if fetched is None:
                skipped.append(f"{source_file} (could not read file)")
                continue
            old_content, sha = fetched
            try:
                new_content = _bump_manifest(
                    item.ecosystem, item.tech_key, item.fixed_version, source_file, old_content
                )
            except FixNotSupported as exc:
                skipped.append(f"{source_file} ({exc})")
                continue
            if new_content == old_content:
                skipped.append(f"{source_file} (dependency not found)")
                continue
            client.update_file(
                repo.full_name,
                source_file,
                branch,
                new_content,
                f"Bitter: bump {item.tech_key} to {item.fixed_version} in {source_file}",
                sha,
            )
            changed_files.append((source_file, f"Bump {item.technology} to {item.fixed_version}."))

        if not changed_files:
            reason = "; ".join(skipped) or "no target file could be located"
            raise FixNotSupported(f"Could not apply the bump to any target file: {reason}")

        if item.ecosystem in ("pypi", "npm") and item.current_version and item.fixed_version:
            try:
                changed_files.extend(_apply_code_migration(client, repo, branch, item))
            except Exception as exc:  # code migration must never break the manifest PR
                log.warning("code migration failed for item %s: %s", item.id, exc)

        pr_body = _build_pr_body(item, changed_files, skipped)
        pr = client.create_pull_request(repo.full_name, item.title, pr_body, branch, repo.default_branch)

    item.fix_pr_url = pr.get("html_url") or pr.get("web_url") or pr.get("url")
    db.commit()
    return item.fix_pr_url
