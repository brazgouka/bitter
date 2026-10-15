"""Application configuration, loaded from environment / .env file.

Read by app.db (DATABASE_URL), app.security (SESSION_SECRET, ENCRYPTION_KEY),
app.services.github (GitHub OAuth), app.services.advisor (Anthropic),
app.services.email (SMTP), and app.main (CORS, frontend URL).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- General ---
    app_name: str = "Bitter — Engineering Intelligence Platform"
    environment: str = "development"
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    # --- Database ---
    # Defaults to a local SQLite file so the app runs with zero external deps.
    # docker-compose overrides this with a Postgres URL.
    database_url: str = "sqlite:///./bitter.db"

    # --- Security ---
    session_secret: str = "dev-insecure-session-secret-change-me"
    # Optional dedicated Fernet key (url-safe base64, 32 bytes). If unset, it is
    # derived deterministically from session_secret (see app.security).
    encryption_key: str | None = None

    # --- GitHub OAuth ---
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # A personal access token for CLI testing (scripts/run_real.py) without the
    # OAuth browser flow. Public repos need no scope; private repos need 'repo'.
    github_token: str | None = None
    github_api_base: str = "https://api.github.com"
    github_oauth_base: str = "https://github.com/login/oauth"
    # Scopes: read access to repo contents + metadata + user email.
    github_oauth_scopes: str = "read:user user:email repo"

    # --- GitLab (personal access token) ---
    # No OAuth app needed: users paste a PAT (scopes: read_api, write_repository)
    # via PUT /api/integrations/gitlab. Override for self-hosted GitLab instances.
    gitlab_api_base: str = "https://gitlab.com/api/v4"

    # --- Anthropic (Claude advisor) ---
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    # When no API key is configured, the advisor falls back to a deterministic
    # heuristic so the pipeline still produces output end-to-end.

    # --- Advisor backend selection ---
    # "auto"      = Claude if ANTHROPIC_API_KEY is set, else heuristic
    # "claude"    = force Anthropic Claude
    # "local"     = OpenAI-compatible local server (Ollama, LM Studio, ...)
    # "heuristic" = deterministic rules, no LLM
    advisor_backend: str = "auto"
    # For advisor_backend="local": an OpenAI-compatible chat-completions endpoint.
    # Ollama default shown; LM Studio uses http://localhost:1234/v1.
    local_model_base_url: str = "http://localhost:11434/v1"
    local_model: str = "llama3.1"
    local_model_api_key: str = "ollama"  # most local servers accept any value

    # --- Email delivery ---
    email_backend: str = "console"  # "console" | "smtp"
    email_from: str = "Bitter <no-reply@bitter.dev>"
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False

    # --- Scheduler ---
    # Daily run time (24h local). The scheduler fires the full pipeline for
    # every user at this hour:minute.
    daily_run_hour: int = 7
    daily_run_minute: int = 0
    enable_scheduler: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_url]

    @property
    def github_callback_url(self) -> str:
        return f"{self.backend_url}/api/auth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
