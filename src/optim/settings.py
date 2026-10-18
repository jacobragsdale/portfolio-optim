"""Process configuration, read once at startup from `OPTIM_*` environment variables. Every field is required. Each
source owns its settings model; nested fields use a double underscore, as in `OPTIM_ORACLE__DSN`."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, RedisDsn, Secret
from pydantic_settings import BaseSettings, SettingsConfigDict

from optim.sources.mssql import MssqlSettings
from optim.sources.oracle import OracleSettings
from optim.sources.rest import ApiSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPTIM_",
        env_nested_delimiter="__",
        env_parse_none_str="none",  # `OPTIM_REDIS_URL=none` turns Redis off explicitly; leaving it unset is an error
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    oracle: OracleSettings
    mssql: MssqlSettings
    signals: ApiSettings
    redis_url: Secret[RedisDsn] | None  # the dataset cache every Job shares; without it, every dataset is read live. Secret: an Azure URL holds the access key
    snapshot_dir: Path
    workers: Annotated[int, Field(ge=1)]  # solver processes per run; 1 solves in the calling process
