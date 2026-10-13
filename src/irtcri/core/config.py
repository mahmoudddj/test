"""Runtime configuration.

All secrets, connection strings and paths come from environment variables
(prefix ``IRTCRI_``) or a ``.env`` file. Nothing sensitive is hard-coded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRTCRI_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"

    # --- Database -------------------------------------------------------
    # SQLite is accepted for dev/test only; staging/prod must use PostgreSQL.
    database_url: str = "sqlite:///./var/irtcri_dev.db"

    # --- Object storage -------------------------------------------------
    object_store: Literal["local", "s3"] = "local"
    local_store_root: Path = Path("./var/objects")
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket_raw: str = "irtcri-raw"
    s3_bucket_derived: str = "irtcri-derived"
    # Server-side encryption header for uploads, e.g. "AES256" (AWS) or "aws:kms"; leave unset on MinIO
    # without KMS and rely on encrypted volumes + bucket default encryption instead.
    s3_sse: str | None = None

    # --- Configuration files -------------------------------------------
    config_dir: Path = REPO_ROOT / "configs"
    data_dir: Path = Path("./var/data")

    # --- Security -------------------------------------------------------
    # Optional one-off token used only to create the first Admin account.
    bootstrap_admin_token: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    token_ttl_days: int = 90

    # --- Governance -----------------------------------------------------
    data_retention_years: int = 15
    # Prospective (real-time) research alerts stay disabled until the model is
    # validated AND an ethics/approval reference is configured.
    prospective_alerts_enabled: bool = False
    prospective_approval_reference: str | None = None

    # --- Imaging --------------------------------------------------------
    qc_config_file: str = "qc/thermal_qc.yaml"
    acquisition_protocol_version: str = "IRT-CRI-ACQ-1.0"

    @model_validator(mode="after")
    def _check_prod(self) -> "Settings":
        if self.env in ("staging", "prod"):
            if self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is not permitted in staging/prod; use PostgreSQL.")
            if self.object_store == "s3" and not (self.s3_access_key and self.s3_secret_key):
                raise ValueError("S3 credentials must be provided via environment variables.")
        return self

    def config_path(self, relative: str) -> Path:
        return Path(self.config_dir) / relative


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
