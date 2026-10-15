"""Tolerant version parsing and comparison.

Detected versions come as raw manifest specs ("^1.2.3", ">=2.0", "==0.115.0",
"v4", "3.11-slim"). We normalise leniently so correlation can compare against
upstream releases and advisory ranges without exploding on odd inputs.
"""
from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

_LEAD = re.compile(r"^[\^~>=<v\s]+")
_NUMERIC = re.compile(r"\d+(?:\.\d+)*")


def parse(v: str | None) -> Version | None:
    if not v:
        return None
    token = re.split(r"[,\s]", v.strip())[0]
    token = _LEAD.sub("", token)
    # docker tags like "3.11-slim" -> "3.11"
    token = token.split("-")[0]
    if not token:
        return None
    try:
        return Version(token)
    except InvalidVersion:
        m = _NUMERIC.match(token)
        if m:
            try:
                return Version(m.group(0))
            except InvalidVersion:
                return None
    return None


def is_older(current: str | None, candidate: str | None) -> bool:
    """True if `current` parses strictly older than `candidate`."""
    a, b = parse(current), parse(candidate)
    if a is None or b is None:
        return False
    return a < b


def in_osv_range(
    version: str | None, introduced: str | None, fixed: str | None
) -> bool:
    """OSV semantics: introduced <= version < fixed (either bound optional)."""
    v = parse(version)
    if v is None:
        return False
    lo = parse(introduced)
    hi = parse(fixed)
    if lo is not None and v < lo:
        return False
    if hi is not None and v >= hi:
        return False
    return True


def major(v: str | None) -> int | None:
    p = parse(v)
    return p.release[0] if p and p.release else None


def is_major_bump(current: str | None, candidate: str | None) -> bool:
    cm, nm = major(current), major(candidate)
    if cm is None or nm is None:
        return False
    return nm > cm


def max_version(versions: list[str | None]) -> str | None:
    """Highest of the given raw version strings (e.g. several fixed_version
    values proposed for the same package). Falls back to the first
    unparsable candidate (e.g. a non-semver identifier) when nothing parses,
    so a caller always gets *something* rather than None outright.
    """
    best_raw: str | None = None
    best_parsed: Version | None = None
    for v in versions:
        if not v:
            continue
        p = parse(v)
        if p is None:
            if best_raw is None:
                best_raw = v
            continue
        if best_parsed is None or p > best_parsed:
            best_parsed, best_raw = p, v
    return best_raw
