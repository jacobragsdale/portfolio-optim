"""Process configuration, read once at startup from `OPTIM_*` environment variables. Every field but the cache root is
required. Each
source owns its settings model; nested fields use a double underscore, as in `OPTIM_ORACLE__DSN`."""

from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from optim.sources.mssql import MssqlSettings
from optim.sources.oracle import OracleSettings
from optim.sources.rest import ApiSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPTIM_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        hide_input_in_errors=True,  # an error would otherwise print every secret beside the field it is missing
    )

    oracle: OracleSettings
    mssql: MssqlSettings
    signals: ApiSettings
    snapshot_root: str  # a path or URL, such as `snapshots` or `abfs://runs@account.dfs.core.windows.net/optim`
    cache_root: str | None = None  # a path or URL for the dataset cache; without it, every dataset is read live
    workers: Annotated[int, Field(ge=1)]  # solver processes per run; 1 solves in the calling process
