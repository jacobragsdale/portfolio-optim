"""Where rows come from. Every source answers `fetch(query, params)` with raw columns, and `optim.data` validates them,
so all sources are held to the same schemas. `optim.context` opens the connections."""

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Literal, Protocol

from pydantic import Field

from optim.schema import Model

type SourceName = Literal["oracle", "mssql", "signals"]
type Param = str | int | float | date | tuple[str, ...]
type Columns = dict[str, list[object]]


class SourceSettings(Model):
    """What every source's settings carry: how hard a run may press it, and how long it may take to answer."""

    max_concurrent: Annotated[int, Field(ge=1)]  # calls this process has open to the source at once; the loader holds the rest
    timeout_s: Annotated[int, Field(ge=1)]  # deadline on every call that crosses the network


class Source(Protocol):
    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns: ...
