"""The AI advisor: turn raw correlation findings into prioritized advice.

The advisor backend is pluggable (settings.advisor_backend):
  * "claude"    — Anthropic Claude (structured tool output)
  * "local"     — any OpenAI-compatible server (Ollama, LM Studio, ...), free
  * "heuristic" — deterministic rules, no LLM
  * "auto"      — Claude if ANTHROPIC_API_KEY is set, else heuristic
Any LLM failure falls back to the heuristic, so the pipeline always yields a report.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.services.correlation import Finding

log = logging.getLogger("bitter.advisor")

SEVERITY_ORDER = {"critical": 0, "medium": 1, "ignore": 2}

# Set by assess() so callers (e.g. scripts/run_real.py) can confirm what ran.
LAST_RUN_BACKEND: str = "heuristic"  # "claude" | "local" | "heuristic"
LAST_RUN_USED_CLAUDE: bool = False  # kept for backwards compatibility

_SYSTEM = (
    "You are Bitter, an AI engineering advisor. You receive findings that "
    "correlate a team's actual technology stack against ecosystem changes "
    "(security advisories, new releases, deprecations, removed APIs). For each "
    "finding assign a severity and write a concise, specific, actionable note.\n\n"
    "Severity rubric:\n"
    "- critical: exploitable security advisory affecting the version in use, a "
    "removed/unsupported API that will break on upgrade, or an end-of-life "
    "component that must be migrated now.\n"
    "- medium: a recommended upgrade, a breaking major release to plan for, or a "
    "deprecation with runway.\n"
    "- ignore: purely informational or low-impact (e.g. a minor release with no "
    "security or breaking impact).\n\n"
    "Be precise. Reference the technology and version. Actions must be concrete "
    "(e.g. 'Upgrade requests to 2.32.0 or later'). Never invent CVEs or versions "
    "beyond what the finding states."
)

_TOOL = {
    "name": "submit_assessment",
    "description": "Return the prioritized assessment for the provided findings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One or two sentences summarizing the day for this engineer.",
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "medium", "ignore"],
                        },
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["index", "severity", "title", "body", "action"],
                },
            },
        },
        "required": ["summary", "items"],
    },
}


@dataclass
class ScoredFinding:
    finding: Finding
    severity: str
    title: str
    body: str
    action: str | None


@dataclass
class Advice:
    summary: str
    items: list[ScoredFinding]


# --------------------------------------------------------------------------
# Heuristic fallback
# --------------------------------------------------------------------------

def _heuristic_severity(f: Finding) -> str:
    if f.kind == "deprecation" and f.tech_key == "kubernetes":
        return "critical"
    if f.kind == "advisory":
        hint = (f.severity_hint or "").upper()
        return "critical" if hint in ("CRITICAL", "HIGH") else "medium"
    if f.kind == "deprecation":
        return "medium"
    if f.kind == "breaking":
        return "medium"
    return "ignore"  # plain release


def _heuristic_action(f: Finding) -> str:
    if f.kind == "advisory":
        if f.fixed_version:
            return f"Upgrade {f.technology} to {f.fixed_version} or later."
        return f"Upgrade {f.technology} to a patched version."
    if f.kind == "deprecation" and f.tech_key == "kubernetes":
        return f"Migrate the affected manifests to {f.fixed_version}."
    if f.kind == "deprecation":
        base = "Plan a migration off the end-of-life release"
        return base + (f"; move to {f.fixed_version}." if f.fixed_version else ".")
    if f.fixed_version:
        return f"Review the changelog and upgrade to {f.technology} {f.fixed_version}."
    return f"Review the {f.technology} changelog and plan an upgrade."


def _heuristic(findings: list[Finding]) -> Advice:
    items = [
        ScoredFinding(
            finding=f,
            severity=_heuristic_severity(f),
            title=f.title,
            body=f.detail,
            action=_heuristic_action(f),
        )
        for f in findings
    ]
    items.sort(key=lambda s: SEVERITY_ORDER.get(s.severity, 3))
    crit = sum(1 for s in items if s.severity == "critical")
    med = sum(1 for s in items if s.severity == "medium")
    summary = (
        f"{crit} critical and {med} medium item(s) need attention across your "
        "monitored repositories." if (crit or med)
        else "No critical or medium items today — your stack looks healthy."
    )
    return Advice(summary=summary, items=items)


# --------------------------------------------------------------------------
# Claude path
# --------------------------------------------------------------------------

def _finding_payload(findings: list[Finding]) -> str:
    import json

    rows = [
        {
            "index": i,
            "repository": f.repository_full_name,
            "technology": f.technology,
            "kind": f.kind,
            "current_version": f.current_version,
            "fixed_version": f.fixed_version,
            "severity_hint": f.severity_hint,
            "title": f.title,
            "detail": f.detail,
        }
        for i, f in enumerate(findings)
    ]
    return json.dumps(rows, indent=2)


def _claude(findings: list[Finding]) -> Advice:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = (
        "Assess the following findings and return them via the submit_assessment "
        "tool. Include every index exactly once.\n\n" + _finding_payload(findings)
    )
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_assessment"},
        messages=[{"role": "user", "content": message}],
    )
    data = None
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_assessment":
            data = block.input
            break
    if not data:
        raise RuntimeError("advisor: no tool_use block returned")

    return _apply(findings, data)


# --------------------------------------------------------------------------
# Shared mapping + local (OpenAI-compatible) path
# --------------------------------------------------------------------------

def _apply(findings: list[Finding], data: dict) -> Advice:
    """Map an LLM's {summary, items:[{index,severity,...}]} onto the findings.

    Robust to a model dropping an index or emitting an invalid severity: those
    fall back to the heuristic per-item, so the report is always complete.
    """
    by_index: dict[int, dict] = {
        int(it["index"]): it
        for it in (data.get("items") or [])
        if isinstance(it, dict) and "index" in it
    }
    scored: list[ScoredFinding] = []
    for i, f in enumerate(findings):
        it = by_index.get(i)
        if it:
            sev = it.get("severity")
            if sev not in SEVERITY_ORDER:
                sev = _heuristic_severity(f)
            scored.append(
                ScoredFinding(
                    finding=f,
                    severity=sev,
                    title=it.get("title") or f.title,
                    body=it.get("body") or f.detail,
                    action=it.get("action") or _heuristic_action(f),
                )
            )
        else:
            scored.append(
                ScoredFinding(
                    finding=f,
                    severity=_heuristic_severity(f),
                    title=f.title,
                    body=f.detail,
                    action=_heuristic_action(f),
                )
            )
    scored.sort(key=lambda s: SEVERITY_ORDER.get(s.severity, 3))
    summary = data.get("summary")
    if not summary:
        crit = sum(1 for s in scored if s.severity == "critical")
        med = sum(1 for s in scored if s.severity == "medium")
        summary = (
            f"{crit} critical and {med} medium item(s) need attention."
            if (crit or med)
            else "No critical or medium items today."
        )
    return Advice(summary=summary, items=scored)


_LOCAL_INSTRUCTIONS = (
    "Return ONLY a single JSON object (no markdown, no commentary) of the form:\n"
    '{"summary": "…", "items": [{"index": <int>, "severity": '
    '"critical"|"medium"|"ignore", "title": "…", "body": "…", "action": "…"}]}\n'
    "Include every index from the input exactly once."
)


def extract_json(text: str) -> dict:
    import json

    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("local advisor: no JSON object in response")
    return json.loads(t[start : end + 1])


def _local(findings: list[Finding]) -> Advice:
    import httpx

    url = settings.local_model_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.local_model_api_key:
        headers["Authorization"] = f"Bearer {settings.local_model_api_key}"
    payload = {
        "model": settings.local_model,
        "messages": [
            {"role": "system", "content": _SYSTEM + "\n\n" + _LOCAL_INSTRUCTIONS},
            {"role": "user", "content": "Findings:\n" + _finding_payload(findings)},
        ],
        "temperature": 0.2,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=240)
    if resp.status_code == 400:  # some servers reject response_format
        payload.pop("response_format", None)
        resp = httpx.post(url, json=payload, headers=headers, timeout=240)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _apply(findings, extract_json(content))


def resolve_backend() -> str:
    backend = (settings.advisor_backend or "auto").lower()
    if backend == "auto":
        return "claude" if settings.anthropic_api_key else "heuristic"
    return backend


def assess(findings: list[Finding], cap: int = 40) -> Advice:
    """Score findings via the configured backend; fall back to the heuristic."""
    global LAST_RUN_USED_CLAUDE, LAST_RUN_BACKEND
    LAST_RUN_USED_CLAUDE = False
    LAST_RUN_BACKEND = "heuristic"
    if not findings:
        return Advice(
            summary="No changes affect your monitored repositories today.", items=[]
        )
    findings = findings[:cap]
    backend = resolve_backend()

    if backend == "claude":
        try:
            advice = _claude(findings)
            LAST_RUN_USED_CLAUDE = True
            LAST_RUN_BACKEND = "claude"
            return advice
        except Exception as exc:  # never let the advisor break the pipeline
            log.warning("Claude advisor failed, using heuristic: %s", exc)
            return _heuristic(findings)

    if backend == "local":
        try:
            advice = _local(findings)
            LAST_RUN_BACKEND = "local"
            return advice
        except Exception as exc:
            log.warning("Local advisor failed (%s); using heuristic", exc)
            return _heuristic(findings)

    return _heuristic(findings)
