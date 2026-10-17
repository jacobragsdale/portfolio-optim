"""Process configuration, read once at startup from `OPTIM_*` environment variables. Every field is required. Each
source owns its settings model; nested fields use a double underscore, as in `OPTIM_ORACLE__DSN`."""

from pathlib import Path

from pydantic import RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

from optim.sources.mssql import MssqlSettings
from optim.sources.oracle import OracleSettings
from optim.sources.rest import ApiSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPTIM_",
        env_nested_delimiter="__",
        env_parse_none_str="none",  # `OPTIM_REDIS_URL=none` turns Redis off explicitly; leaving it unset is an error
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    oracle: OracleSettings
    mssql: MssqlSettings
    signals: ApiSettings
    oms: ApiSettings
    redis_url: RedisDsn | None  # without Redis, datasets are cached in process memory
    snapshot_dir: Path
