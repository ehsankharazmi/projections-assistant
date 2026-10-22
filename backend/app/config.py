"""Application settings, loaded from environment variables (and an optional .env file).

Invalid combinations are rejected at startup rather than silently degraded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Storage -----------------------------------------------------------------
    data_dir: Path = Path("data")
    jobs_dir: Path | None = None  # shared with the calculation worker; defaults to <data_dir>/jobs
    demo_dir: Path | None = None  # generated demo artifacts; defaults to <data_dir>/demo

    # --- Upload and inspection limits ------------------------------------------
    max_upload_bytes: int = 10 * 1024 * 1024
    max_populated_cells: int = 100_000
    max_zip_members: int = 2_000
    max_uncompressed_bytes: int = 150 * 1024 * 1024
    max_member_compression_ratio: float = 200.0
    inspection_timeout_seconds: float = 30.0

    # --- Calculation worker ----------------------------------------------------
    worker_mode: Literal["external", "inprocess"] = "external"
    calc_timeout_seconds: float = 90.0
    calc_wait_in_turn_seconds: float = 20.0
    soffice_path: str = "soffice"

    # --- Comparison tolerances -------------------------------------------------
    abs_tolerance: float = 1e-8
    rel_tolerance: float = 1e-6

    # --- Authentication --------------------------------------------------------
    auth_mode: Literal["local", "cloudflare_access"] = "local"
    deployment_profile: Literal["local", "remote"] = "local"
    local_dev_user_email: str = "owner@localhost"
    allowed_origin: str = "http://127.0.0.1:8080"
    cf_access_team_domain: str = ""  # e.g. https://<team>.cloudflareaccess.com
    cf_access_audience: str = ""
    allowed_emails: str = ""  # comma-separated allowlist for remote access
    owner_email: str = ""  # owner-only pages (usage, diagnostics); defaults to local dev user in local mode

    # --- AI ----------------------------------------------------------------------
    ai_mode: Literal["demo", "anthropic"] = "demo"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Effort = "medium"
    anthropic_refusal_fallback: Literal["default", "off"] = "default"
    ai_max_output_tokens: int = Field(8000, ge=256, le=64000)
    ai_max_model_rounds: int = Field(6, ge=1, le=20)
    ai_max_tool_calls_per_turn: int = Field(12, ge=1, le=50)
    ai_timeout_seconds: float = Field(120.0, gt=0)
    ai_max_retries: int = Field(2, ge=0, le=5)
    ai_requests_per_user_per_minute: int = Field(10, ge=1)
    ai_max_concurrent_turns: int = Field(1, ge=1)
    ai_daily_budget_usd: float = Field(2.00, ge=0)
    ai_monthly_budget_usd: float = Field(20.00, ge=0)
    ai_pricing_file: Path = Path(__file__).parent / "ai" / "pricing.json"
    ai_history_turns: int = Field(8, ge=1, le=50)

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.jobs_dir is None:
            self.jobs_dir = self.data_dir / "jobs"
        if self.demo_dir is None:
            self.demo_dir = self.data_dir / "demo"
        if self.ai_mode == "anthropic":
            if not self.anthropic_api_key.get_secret_value().strip():
                raise ValueError("AI_MODE=anthropic requires ANTHROPIC_API_KEY")
            if not self.anthropic_model.strip():
                raise ValueError("AI_MODE=anthropic requires ANTHROPIC_MODEL")
        if self.deployment_profile == "remote" and self.auth_mode != "cloudflare_access":
            raise ValueError("The remote profile refuses local authentication; set AUTH_MODE=cloudflare_access")
        if self.auth_mode == "cloudflare_access":
            missing = [name for name, value in (("CF_ACCESS_TEAM_DOMAIN", self.cf_access_team_domain),
                                                ("CF_ACCESS_AUDIENCE", self.cf_access_audience),
                                                ("ALLOWED_EMAILS", self.allowed_emails)) if not value.strip()]
            if missing:
                raise ValueError(f"AUTH_MODE=cloudflare_access requires {', '.join(missing)}")
            if not self.cf_access_team_domain.startswith("https://"):
                raise ValueError("CF_ACCESS_TEAM_DOMAIN must be an https:// URL")
        return self

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def effective_owner_email(self) -> str:
        if self.owner_email:
            return self.owner_email.lower()
        if self.auth_mode == "local":
            return self.local_dev_user_email.lower()
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
