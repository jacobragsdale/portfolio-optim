"""Where rows come from: Oracle, SQL Server and REST, behind one `fetch(query, params) -> columns` call.

Sources only move bytes. Validation and dtypes happen in `data.py`, so every source is held to the same schema.
"""

import json
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from functools import cache, partial
from importlib import resources
from typing import Literal, Protocol

import httpx
import mssql_python
import oracledb
from pydantic import TypeAdapter

from optim.settings import Settings

type SourceName = Literal["oracle", "mssql", "signals"]
type Param = str | int | float | date | tuple[str, ...]
type Columns = dict[str, list[object]]

LOGIN_TIMEOUT_S = 30
QUERY_TIMEOUT_S = 120


class Source(Protocol):
    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns: ...


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
    """Any DB-API driver: python-oracledb (`:name` binds) or mssql-python (`%(name)s` binds).

    `query` names a file in `optim/queries/`. A tuple param binds as one JSON array string, which the SQL unpacks with
    JSON_TABLE (Oracle) or OPENJSON (SQL Server): one bind per batch, no IN-list limits, no SQL built from strings.
    """

    connect: Callable[[], Connection]

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        binds: dict[str, object] = {k: json.dumps(v) if isinstance(v, tuple) else v for k, v in params.items()}
        with closing(self.connect()) as connection, closing(connection.cursor()) as cursor:
            cursor.arraysize = 10_000  # fewer network round trips on large pulls
            cursor.execute(_sql(query), binds)
            names = [str(column[0]).lower() for column in cursor.description or ()]
            rows = cursor.fetchall()
        if not rows:
            return {name: [] for name in names}
        return {name: list(values) for name, values in zip(names, zip(*rows, strict=True), strict=True)}


@cache
def _sql(name: str) -> str:
    return resources.files("optim").joinpath("queries", name).read_text()


_RECORDS = TypeAdapter(list[dict[str, object]])


@dataclass(frozen=True)
class Rest:
    """A JSON API that answers `GET query` with a list of records."""

    client: httpx.Client  # base URL, auth, timeout and retries are configured where it is built

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        response = self.client.get(query, params={k: _query_value(v) for k, v in params.items()})
        response.raise_for_status()
        records = _RECORDS.validate_json(response.content)
        names = list(records[0]) if records else []
        if any(record.keys() != records[0].keys() for record in records):
            msg = f"{query}: records do not all have the same fields"
            raise ValueError(msg)
        return {name: [record[name] for record in records] for name in names}


def _query_value(value: Param) -> str | int | float | list[str]:
    match value:
        case date():
            return value.isoformat()
        case tuple():
            return list(value)
        case _:
            return value


@contextmanager
def open_sources(settings: Settings) -> Generator[dict[SourceName, Source]]:
    """Connect each source once per process, and close them on exit."""
    pool = oracledb.create_pool(user=settings.oracle_user, password=settings.oracle_password.get_secret_value(), dsn=settings.oracle_dsn, min=1, max=8)
    signals = httpx.Client(base_url=str(settings.signals_url), timeout=QUERY_TIMEOUT_S, transport=httpx.HTTPTransport(retries=3))
    try:
        yield {"oracle": Sql(partial(_oracle, pool)), "mssql": Sql(partial(_mssql, settings.mssql_connection_string.get_secret_value())), "signals": Rest(signals)}
    finally:
        signals.close()
        pool.close()


def _oracle(pool: oracledb.ConnectionPool) -> oracledb.Connection:
    connection = pool.acquire()
    connection.call_timeout = QUERY_TIMEOUT_S * 1000
    return connection


def _mssql(connection_string: str) -> mssql_python.Connection:
    connection = mssql_python.connect(connection_string, timeout=LOGIN_TIMEOUT_S)
    connection.timeout = QUERY_TIMEOUT_S
    return connection
