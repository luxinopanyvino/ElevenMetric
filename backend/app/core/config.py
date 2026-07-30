"""Application settings, loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ELEVENMETRIC_", env_file=".env", extra="ignore"
    )

    app_name: str = "ElevenMetric"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # --- Persistence -------------------------------------------------------
    database_url: str = f"sqlite:///{PROJECT_DIR / 'data' / 'elevenmetric.db'}"

    # --- Auth --------------------------------------------------------------
    secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12

    # --- Multitenancy ------------------------------------------------------
    tenant_header: str = "X-Tenant"
    # A tenant may be pinned per-request via header, but the JWT claim always wins
    # unless the user is a platform superuser.
    allow_header_tenant_override: bool = True

    # --- Media / uploads ---------------------------------------------------
    media_root: Path = PROJECT_DIR / "data" / "media"
    max_upload_mb: int = 2048

    # --- CORS --------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # --- Analytics defaults ------------------------------------------------
    pitch_length_m: float = 105.0
    pitch_width_m: float = 68.0

    # --- External data sources ---------------------------------------------
    #
    # Off by default. When enabled the API makes outbound requests to the hosts
    # listed in `external_hosts` on behalf of the operator, who is responsible
    # for their own compliance with those sites' terms. Nothing is fetched
    # unless a user explicitly asks for an import; there is no background job.
    # See docs/EXTERNAL_SOURCES.md.
    external_fetch_enabled: bool = False
    external_hosts: list[str] = Field(
        default_factory=lambda: ["sofifa.com", "github.com", "githubusercontent.com"]
    )
    #: Seconds between consecutive requests to the same host.
    external_rate_limit_s: float = 1.0
    external_timeout_s: float = 20.0
    #: Fetched responses are cached here with their retrieval timestamp, so a
    #: repeated preview costs nothing and every row can say when it was read.
    external_cache_dir: Path = PROJECT_DIR / "data" / "external-cache"
    external_cache_ttl_hours: float = 24.0
    #: Sent on every outbound request. Identify your deployment honestly.
    external_user_agent: str = (
        "ElevenMetric/1.0 (self-hosted football analysis; "
        "+https://github.com/luxinopanyvino/ElevenMetric)"
    )

    @field_validator("cors_origins", "external_hosts", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def sqlite_path(self) -> Path | None:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", "", 1))
        return None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if (p := settings.sqlite_path) is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
