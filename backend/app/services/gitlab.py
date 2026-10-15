"""GitLab REST client (personal access token based).

Mirrors the method surface of app.services.github.GitHubClient so app.pipeline
and app.services.fixer can treat both providers interchangeably via
app.services.providers.client_for. Auth is a PAT the user pastes in Settings
(app.api's /api/integrations/gitlab route) rather than an OAuth flow, so
there is no token-exchange step here.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.config import settings


class GitLabError(RuntimeError):
    pass


@dataclass
class GitLabRepo:
    id: int
    full_name: str
    default_branch: str
    private: bool


def _enc(value: str) -> str:
    return quote(value, safe="")


class GitLabClient:
    """Authenticated GitLab REST client scoped to one user's personal access token."""

    def __init__(self, token: str):
        self._client = httpx.Client(
            base_url=settings.gitlab_api_base,
            headers={"PRIVATE-TOKEN": token},
            timeout=30,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitLabClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_user(self) -> dict:
        r = self._client.get("/user")
        r.raise_for_status()
        return r.json()

    def list_repositories(self, max_repos: int = 100) -> list[GitLabRepo]:
        repos: list[GitLabRepo] = []
        page = 1
        while len(repos) < max_repos:
            r = self._client.get(
                "/projects",
                params={
                    "per_page": 100,
                    "page": page,
                    "membership": "true",
                    "order_by": "last_activity_at",
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for it in batch:
                repos.append(
                    GitLabRepo(
                        id=it["id"],
                        full_name=it["path_with_namespace"],
                        default_branch=it.get("default_branch") or "main",
                        private=it.get("visibility") != "public",
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return repos[:max_repos]

    def get_repo(self, full_name: str) -> GitLabRepo:
        r = self._client.get(f"/projects/{_enc(full_name)}")
        r.raise_for_status()
        it = r.json()
        return GitLabRepo(
            id=it["id"],
            full_name=it["path_with_namespace"],
            default_branch=it.get("default_branch") or "main",
            private=it.get("visibility") != "public",
        )

    def list_paths(self, full_name: str, ref: str) -> list[str]:
        """All blob paths in the repo via the recursive repository-tree API."""
        paths: list[str] = []
        page = 1
        while True:
            r = self._client.get(
                f"/projects/{_enc(full_name)}/repository/tree",
                params={"ref": ref, "recursive": "true", "per_page": 100, "page": page},
            )
            if r.status_code != 200:
                return paths
            batch = r.json()
            if not batch:
                break
            paths.extend(it["path"] for it in batch if it.get("type") == "blob")
            if len(batch) < 100:
                break
            page += 1
        return paths

    def get_file(self, full_name: str, path: str, ref: str | None = None) -> str | None:
        """Decoded text of a single file, or None if missing/binary."""
        r = self._client.get(
            f"/projects/{_enc(full_name)}/repository/files/{_enc(path)}",
            params={"ref": ref or "HEAD"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None

    # -- Write operations, used by app.services.fixer to open "propose fix" MRs --

    def get_file_with_sha(
        self, full_name: str, path: str, ref: str | None = None
    ) -> tuple[str, str] | None:
        """(decoded content, "") — GitLab's file-update API needs no blob sha,
        unlike GitHub's; the empty string keeps the call signature symmetric.
        """
        content = self.get_file(full_name, path, ref)
        if content is None:
            return None
        return content, ""

    def get_ref_sha(self, full_name: str, branch: str) -> str:
        r = self._client.get(
            f"/projects/{_enc(full_name)}/repository/branches/{_enc(branch)}"
        )
        r.raise_for_status()
        return r.json()["commit"]["id"]

    def create_branch(self, full_name: str, new_branch: str, from_sha: str) -> None:
        """Create a branch pointing at from_sha. No-op if it already exists."""
        r = self._client.post(
            f"/projects/{_enc(full_name)}/repository/branches",
            params={"branch": new_branch, "ref": from_sha},
        )
        if r.status_code == 400:  # "Branch already exists"
            return
        r.raise_for_status()

    def update_file(
        self,
        full_name: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        sha: str = "",
    ) -> None:
        r = self._client.put(
            f"/projects/{_enc(full_name)}/repository/files/{_enc(path)}",
            json={"branch": branch, "content": content, "commit_message": message},
        )
        r.raise_for_status()

    def create_pull_request(
        self,
        full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
    ) -> dict:
        """Opens a GitLab merge request. Named create_pull_request (not
        create_merge_request) so app.services.fixer can call either provider
        client through one interface.
        """
        r = self._client.post(
            f"/projects/{_enc(full_name)}/merge_requests",
            json={
                "source_branch": head_branch,
                "target_branch": base_branch,
                "title": title,
                "description": body,
            },
        )
        if r.status_code == 409:
            # An MR from this branch may already exist; find and return it.
            existing = self._client.get(
                f"/projects/{_enc(full_name)}/merge_requests",
                params={"source_branch": head_branch, "state": "opened"},
            )
            existing.raise_for_status()
            items = existing.json()
            if items:
                return items[0]
        r.raise_for_status()
        return r.json()
