"""Curated registry mapping technologies to their upstream release/EOL sources.

Used by ingestion (which GitHub repo publishes releases, which endoflife.date
product tracks EOL) and by correlation/advisor for human-friendly names.

This is intentionally a small, hand-curated seed set covering the technologies
named in the product spec. New entries slot in without code changes elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TechInfo:
    key: str  # canonical key; matches StackComponent.name where possible
    display_name: str
    ecosystem: str  # pypi/npm/docker/kubernetes/terraform/github-actions/maven
    github_repo: str | None = None  # "owner/name" for GitHub Releases ingestion
    endoflife_product: str | None = None  # endoflife.date product slug
    aliases: tuple[str, ...] = field(default_factory=tuple)


TECHNOLOGIES: list[TechInfo] = [
    # --- Python (pypi) ---
    TechInfo("fastapi", "FastAPI", "pypi", github_repo="fastapi/fastapi"),
    TechInfo("django", "Django", "pypi", github_repo="django/django", endoflife_product="django"),
    TechInfo("flask", "Flask", "pypi", github_repo="pallets/flask"),
    TechInfo("requests", "Requests", "pypi", github_repo="psf/requests"),
    TechInfo("sqlalchemy", "SQLAlchemy", "pypi", github_repo="sqlalchemy/sqlalchemy"),
    TechInfo("pydantic", "Pydantic", "pypi", github_repo="pydantic/pydantic"),
    TechInfo("celery", "Celery", "pypi", github_repo="celery/celery"),
    TechInfo("numpy", "NumPy", "pypi", github_repo="numpy/numpy", endoflife_product="numpy"),
    TechInfo("boto3", "AWS SDK (boto3)", "pypi", github_repo="boto/boto3", aliases=("aws-sdk",)),
    # --- JavaScript / TypeScript (npm) ---
    TechInfo("react", "React", "npm", github_repo="facebook/react"),
    TechInfo("next", "Next.js", "npm", github_repo="vercel/next.js", endoflife_product="nextjs", aliases=("nextjs", "next.js")),
    TechInfo("express", "Express", "npm", github_repo="expressjs/express"),
    TechInfo("typescript", "TypeScript", "npm", github_repo="microsoft/TypeScript", endoflife_product="typescript"),
    TechInfo("axios", "Axios", "npm", github_repo="axios/axios"),
    TechInfo("vue", "Vue", "npm", github_repo="vuejs/core", endoflife_product="vue"),
    # --- Java (maven) ---
    TechInfo("spring-boot", "Spring Boot", "maven", github_repo="spring-projects/spring-boot", endoflife_product="spring-boot", aliases=("spring boot", "springboot")),
    # --- Infrastructure ---
    TechInfo("kubernetes", "Kubernetes", "kubernetes", github_repo="kubernetes/kubernetes", endoflife_product="kubernetes", aliases=("k8s",)),
    TechInfo("terraform", "Terraform", "terraform", github_repo="hashicorp/terraform", endoflife_product="terraform"),
    # --- Container base images / services (docker) ---
    TechInfo("postgres", "PostgreSQL", "docker", endoflife_product="postgresql", aliases=("postgresql",)),
    TechInfo("redis", "Redis", "docker", github_repo="redis/redis", endoflife_product="redis"),
    TechInfo("nginx", "nginx", "docker", endoflife_product="nginx"),
    TechInfo("node", "Node.js", "docker", endoflife_product="nodejs", aliases=("nodejs",)),
    TechInfo("python", "Python", "docker", endoflife_product="python"),
    TechInfo("mysql", "MySQL", "docker", endoflife_product="mysql"),
    TechInfo("mongo", "MongoDB", "docker", endoflife_product="mongodb", aliases=("mongodb",)),
]

_ALIAS: dict[str, TechInfo] = {}
for _t in TECHNOLOGIES:
    _ALIAS[_t.key.lower()] = _t
    _ALIAS[_t.display_name.lower()] = _t
    for _a in _t.aliases:
        _ALIAS[_a.lower()] = _t


def lookup(name: str) -> TechInfo | None:
    return _ALIAS.get(name.strip().lower())


def display_name(name: str) -> str:
    info = lookup(name)
    return info.display_name if info else name


def github_tracked() -> list[TechInfo]:
    """Technologies whose releases we ingest from the GitHub Releases API."""
    return [t for t in TECHNOLOGIES if t.github_repo]


def endoflife_tracked() -> list[TechInfo]:
    """Technologies whose EOL/deprecation we ingest from endoflife.date."""
    return [t for t in TECHNOLOGIES if t.endoflife_product]
