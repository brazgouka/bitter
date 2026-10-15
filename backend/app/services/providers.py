"""Picks the right Git-host client for a user + provider.

Used by app.pipeline (sync/scan) and app.services.fixer (propose-fix) so
neither has to branch on provider itself — Repository.provider says which
client to build, and GitHubClient / GitLabClient share a method surface.
"""
from __future__ import annotations

from app.models import User
from app.security import decrypt_token
from app.services.github import GitHubClient
from app.services.gitlab import GitLabClient

PROVIDERS = ("github", "gitlab")


class ProviderNotConnected(Exception):
    pass


def client_for(user: User, provider: str) -> GitHubClient | GitLabClient:
    if provider == "gitlab":
        if not user.gitlab_token_enc:
            raise ProviderNotConnected("GitLab is not connected for this user.")
        return GitLabClient(decrypt_token(user.gitlab_token_enc))
    if not user.access_token_enc:
        raise ProviderNotConnected("GitHub is not connected for this user.")
    return GitHubClient(decrypt_token(user.access_token_enc))
