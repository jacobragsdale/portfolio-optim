"""Where rows come from. Every source answers `fetch(query, params)` with raw columns, and `optim.data` validates them,
so all sources are held to the same schemas. `optim.context` opens the connections."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Literal, Protocol

type SourceName = Literal["oracle", "mssql", "signals"]
type Param = str | int | float | date | tuple[str, ...]
type Columns = dict[str, list[object]]

TIMEOUT_S = 120  # deadline on every call that crosses the network


class Source(Protocol):
    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns: ...


def columns_from_rows(names: Sequence[str], rows: Sequence[Iterable[object]]) -> Columns:
    """Transpose row tuples, as a database cursor returns them, into columns."""
    if not rows:
        return {name: [] for name in names}
    return {name: list(values) for name, values in zip(names, zip(*rows, strict=True), strict=True)}


def columns_from_records(records: Sequence[Mapping[str, object]], *, source: str) -> Columns:
    """Transpose records, as a JSON API returns them, into columns. Every record must have the same fields."""
    names = list(records[0]) if records else []
    if any(record.keys() != records[0].keys() for record in records):
        msg = f"{source}: records do not all have the same fields"
        raise ValueError(msg)
    return {name: [record[name] for record in records] for name in names}
