"""Any DB-API driver as a `Source`. Queries are SQL files, named by their path inside the `optim` package.

A tuple param binds as one JSON array string, which the SQL unpacks with JSON_TABLE (Oracle) or OPENJSON (SQL Server):
one bind per batch, no IN-list limits, and no SQL built from strings.
"""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Protocol

from optim.sources import Columns, Param, columns_from_rows


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
        with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
            cursor.arraysize = 10_000  # fewer network round trips on large pulls
            cursor.execute(_read(query), binds)
            names = [str(column[0]).lower() for column in cursor.description or ()]
            return columns_from_rows(names, cursor.fetchall())


@cache
def _read(path: str) -> str:
    return resources.files("optim").joinpath(path).read_text()
