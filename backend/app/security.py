"""Symmetric encryption for the stored GitHub access token.

Imported by app.api (auth callback stores the token) and app.services.github
(reads it back to call the GitHub API on the user's behalf).
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        # Derive a stable Fernet key from the session secret so dev works
        # without extra config. Set ENCRYPTION_KEY in production.
        digest = hashlib.sha256(settings.session_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(token_enc: str) -> str:
    return _fernet().decrypt(token_enc.encode()).decode()
