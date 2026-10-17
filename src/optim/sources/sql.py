"""Any DB-API driver as a `Source`. Queries are SQL files, named by their path inside the `optim` package.

A tuple param binds as one JSON array string, which the SQL unpacks with JSON_TABLE (Oracle) or OPENJSON (SQL Server):
one bind per batch, no IN-list limits, and no SQL built from strings.
"""

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Protocol

from optim.logs import ms_since
from optim.sources import Columns, Param, columns_from_rows

log = logging.getLogger(__name__)


class _Column(Protocol):
    def __getitem__(self, index: int, /) -> object: ...


class Cursor(Protocol):
    arraysize: int

    @property
    def description(self) -> Sequence[_Column] | None: ...
    def execute(self, statement: str, parameters: dict[str, object], /) -> object: ...
    def fetchall(self) -> Sequence[Iterable[object]]: ...
    def close(self) -> object: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def close(self) -> object: ...


@dataclass(frozen=True)
class Sql:
    connect: Callable[[], Connection]

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        binds: dict[str, object] = {k: json.dumps(v) if isinstance(v, tuple) else v for k, v in params.items()}
        start = time.perf_counter()
        with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
            connected = ms_since(start)
            cursor.arraysize = 10_000  # fewer network round trips on large pulls
            cursor.execute(read_sql(query), binds)
            executed = ms_since(start)
            names = [str(column[0]).lower() for column in cursor.description or ()]
            rows = cursor.fetchall()
        timings = {"connect_ms": connected, "execute_ms": round(executed - connected, 1), "fetch_ms": round(ms_since(start) - executed, 1)}
        log.debug("query", extra={"query": query, "rows": len(rows)} | timings)
        return columns_from_rows(names, rows)


@cache
def read_sql(path: str) -> str:
    """A SQL file's text, by its path inside the `optim` package."""
    return resources.files("optim").joinpath(path).read_text()
