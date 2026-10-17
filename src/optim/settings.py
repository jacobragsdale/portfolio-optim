"""Process configuration, read once at startup from `OPTIM_*` environment variables. Every field is required."""

from pathlib import Path

from pydantic import HttpUrl, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPTIM_",
        env_parse_none_str="none",  # `OPTIM_REDIS_URL=none` turns Redis off explicitly; leaving it unset is an error
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    oracle_dsn: str
    oracle_user: str
    oracle_password: SecretStr
    mssql_connection_string: SecretStr
    signals_url: HttpUrl
    oms_url: HttpUrl
    redis_url: RedisDsn | None
    snapshot_dir: Path
