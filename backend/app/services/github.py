"""GitHub OAuth token exchange + a thin REST client.

Used by app.api (auth callback, repo sync) and app.pipeline (fetching files to
fingerprint). Sync httpx keeps the whole pipeline synchronous and simple.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from app.config import settings


class GitHubError(RuntimeError):
    pass


@dataclass
class GitHubRepo:
    id: int
    full_name: str
    default_branch: str
    private: bool


def exchange_code_for_token(code: str) -> str:
    """Exchange an OAuth callback code for a user access token."""
    resp = httpx.post(
        f"{settings.github_oauth_base}/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": settings.github_callback_url,
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise GitHubError(f"OAuth token exchange failed: {payload}")
    return token


class GitHubClient:
    """Authenticated GitHub REST client scoped to one user's token."""

    def __init__(self, token: str):
        self._client = httpx.Client(
            base_url=settings.github_api_base,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_user(self) -> dict:
        r = self._client.get("/user")
        r.raise_for_status()
        return r.json()

    def get_primary_email(self) -> str | None:
        r = self._client.get("/user/emails")
        if r.status_code != 200:
            return None
        for entry in r.json():
            if entry.get("primary"):
                return entry.get("email")
        return None

    def list_repositories(self, max_repos: int = 100) -> list[GitHubRepo]:
        repos: list[GitHubRepo] = []
        page = 1
        while len(repos) < max_repos:
            r = self._client.get(
                "/user/repos",
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for it in batch:
                repos.append(
                    GitHubRepo(
                        id=it["id"],
                        full_name=it["full_name"],
                        default_branch=it.get("default_branch") or "main",
                        private=it.get("private", False),
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return repos[:max_repos]

    def get_repo(self, full_name: str) -> GitHubRepo:
        r = self._client.get(f"/repos/{full_name}")
        r.raise_for_status()
        it = r.json()
        return GitHubRepo(
            id=it["id"],
            full_name=it["full_name"],
            default_branch=it.get("default_branch") or "main",
            private=it.get("private", False),
        )

    def list_paths(self, full_name: str, ref: str) -> list[str]:
        """All blob paths in the repo via the recursive git-tree API."""
        r = self._client.get(
            f"/repos/{full_name}/git/trees/{ref}", params={"recursive": "1"}
        )
        if r.status_code != 200:
            return []
        tree = r.json().get("tree", [])
        return [t["path"] for t in tree if t.get("type") == "blob"]

    def get_file(self, full_name: str, path: str, ref: str | None = None) -> str | None:
        """Decoded text of a single file, or None if missing/binary/dir."""
        params = {"ref": ref} if ref else None
        r = self._client.get(
            f"/repos/{full_name}/contents/{path}", params=params
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):  # path is a directory
            return None
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None

    # -- Write operations, used by app.services.fixer to open "propose fix" PRs --

    def get_file_with_sha(
        self, full_name: str, path: str, ref: str | None = None
    ) -> tuple[str, str] | None:
        """(decoded content, blob sha) for a file — the sha is required to update it."""
        params = {"ref": ref} if ref else None
        r = self._client.get(f"/repos/{full_name}/contents/{path}", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) or data.get("encoding") != "base64":
            return None
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return content, data["sha"]

    def get_ref_sha(self, full_name: str, branch: str) -> str:
        r = self._client.get(f"/repos/{full_name}/git/ref/heads/{branch}")
        r.raise_for_status()
        return r.json()["object"]["sha"]

    def create_branch(self, full_name: str, new_branch: str, from_sha: str) -> None:
        """Create a branch pointing at from_sha. No-op if it already exists."""
        r = self._client.post(
            f"/repos/{full_name}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
        )
        if r.status_code == 422:  # "Reference already exists"
            return
        r.raise_for_status()

    def update_file(
        self,
        full_name: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        sha: str,
    ) -> None:
        r = self._client.put(
            f"/repos/{full_name}/contents/{path}",
            json={
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode(),
                "branch": branch,
                "sha": sha,
            },
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
        r = self._client.post(
            f"/repos/{full_name}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            },
        )
        if r.status_code == 422:
            # A PR from this branch may already exist; find and return it.
            existing = self._client.get(
                f"/repos/{full_name}/pulls",
                params={"head": f"{full_name.split('/')[0]}:{head_branch}", "state": "open"},
            )
            existing.raise_for_status()
            items = existing.json()
            if items:
                return items[0]
        r.raise_for_status()
        return r.json()
