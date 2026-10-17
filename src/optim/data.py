"""Loading: a `Dataset` pairs a pydantic row schema with a source query and a cache rule; `Loader` turns it into a
validated, typed DataFrame.

Rows are validated column by column, with one pydantic `TypeAdapter(list[T])` per field. That is about 5x faster than
building a model per row (100k bonds: ~85ms vs ~460ms), at one cost: field rules must live in the annotation
(`Annotated[float, Field(gt=0)]`, `AfterValidator`). `@field_validator` and `@model_validator` are rejected, and
cross-row or cross-column rules go in `Dataset.check`.
"""

import functools
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import batched
from types import NoneType
from typing import Annotated, Literal, get_args, get_origin

import pandas as pd
from pydantic import BaseModel, BeforeValidator, ConfigDict, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from optim.cache import Cache
from optim.sources import Columns, Param, Source, SourceName
from optim.util import timed

STRICT = ConfigDict(strict=True, extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class Strict(BaseModel):
    """Base for every model that crosses a boundary: strategy JSON, database rows, outbound orders."""

    model_config = STRICT


class Row(Strict):
    """Schema of one dataset row. Field names are column names; annotations pick the pandas dtype (see `_dtype`)."""


def _as_date(value: object) -> object:
    """Oracle DATE (and pandas) hand back dates as midnight datetimes; JSON sends ISO strings."""
    if isinstance(value, datetime) and value.tzinfo is None and value.time() == time():
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


Date = Annotated[date, BeforeValidator(_as_date)]

type Dtype = str | pd.CategoricalDtype


@dataclass(frozen=True)
class Dataset:
    name: str
    row: type[Row]
    source: SourceName
    query: str  # a file in optim/queries/, or a REST path
    key: tuple[str, ...]  # columns that must be unique
    ttl: timedelta | None = None  # how long it may be cached; None means always read live
    check: Callable[[pd.DataFrame], None] | None = None  # cross-row rules; raise ValueError


@dataclass(frozen=True)
class Loader:
    """Fetches datasets through their sources, with an optional cache. Thread-safe, so independent datasets can load
    concurrently."""

    sources: Mapping[SourceName, Source]
    cache: Cache | None = None

    def load(self, dataset: Dataset, **params: Param) -> pd.DataFrame:
        if self.cache is None or dataset.ttl is None:
            return self._fetch(dataset, params)
        key = _cache_key(dataset, params)
        if (hit := self.cache.get(key)) is not None:
            return hit
        frame = self._fetch(dataset, params)
        self.cache.set(key, frame, dataset.ttl)
        return frame

    def load_batched(self, dataset: Dataset, param: str, ids: Sequence[str], *, size: int = 1000, **params: Param) -> pd.DataFrame:
        """Load `ids` in chunks of `size`, one query per chunk, binding each chunk to `param`."""
        frames = [self.load(dataset, **params, **{param: chunk}) for chunk in batched(ids, size, strict=False)]
        return pd.concat(frames, ignore_index=True) if frames else empty_frame(dataset.row)

    def _fetch(self, dataset: Dataset, params: Mapping[str, Param]) -> pd.DataFrame:
        with timed("load", dataset=dataset.name):
            return to_frame(dataset, self.sources[dataset.source].fetch(dataset.query, params))


def to_frame(dataset: Dataset, columns: Columns) -> pd.DataFrame:
    """Validate raw columns against the row schema and build a typed frame. Errors name the dataset, column and row."""
    if not columns:  # an empty REST response carries no column names
        return empty_frame(dataset.row)
    schema = _schema(dataset.row)
    if columns.keys() != schema.keys():
        msg = f"{dataset.name}: expected columns {sorted(schema)}, got {sorted(columns)}"
        raise ValueError(msg)
    frame = pd.DataFrame({name: _column(dataset.name, name, columns[name], *schema[name]) for name in schema})
    if (duplicated := frame.duplicated(list(dataset.key))).any():
        examples = frame.loc[duplicated, list(dataset.key)].head(3).to_dict("records")
        msg = f"{dataset.name}: duplicate {dataset.key}, e.g. {examples}"
        raise ValueError(msg)
    if dataset.check is not None:
        dataset.check(frame)
    return frame


def _column(dataset: str, name: str, values: list[object], adapter: TypeAdapter[list[object]], dtype: Dtype) -> pd.Series:
    try:
        return pd.Series(adapter.validate_python(values), dtype=dtype)
    except ValidationError as error:
        first = error.errors()[0]
        msg = f"{dataset}.{name} row {first['loc'][0]}: {first['msg']} (got {first['input']!r}); {error.error_count()} bad values"
        raise ValueError(msg) from None  # the first error and the count; chaining all of them can flood a log


def empty_frame(row: type[Row]) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dtype) for name, (_, dtype) in _schema(row).items()})


def to_models[R: Row](row: type[R], frame: pd.DataFrame) -> list[R]:
    """A validated frame's rows as models, for small frames you want typed attributes on (one per account)."""
    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    return [row.model_validate(record) for record in records]


@functools.cache
def _schema(row: type[Row]) -> dict[str, tuple[TypeAdapter[list[object]], Dtype]]:
    validators = row.__pydantic_decorators__
    if validators.field_validators or validators.model_validators:
        msg = f"{row.__name__}: rows are validated column-wise; put validators in Annotated[...] metadata"
        raise TypeError(msg)
    return {name: (_column_adapter(field), _dtype(field.annotation)) for name, field in row.model_fields.items()}


def _column_adapter(field: FieldInfo) -> TypeAdapter[list[object]]:
    """Validates a whole column: the field's type and constraints, applied to every element."""
    return TypeAdapter(list[Annotated[field.annotation, field]], config=STRICT)  # ty: ignore[invalid-type-form] -- built from the row schema at runtime


def _dtype(annotation: object) -> Dtype:
    """str -> str, float -> float64, int -> int64 (Int64 if optional), bool, date -> datetime64, Literal -> ordered
    category in declared order (so `rating <= "A-"` means "A- or better")."""
    optional = NoneType in get_args(annotation)
    base = next(arg for arg in get_args(annotation) if arg is not NoneType) if optional else annotation
    if get_origin(base) is Annotated:  # `State | None`: constraints sit inside the union
        base = get_args(base)[0]
    if get_origin(base) is Literal:
        return pd.CategoricalDtype(list(get_args(base)), ordered=True)
    dtypes: dict[object, Dtype] = {str: "str", float: "float64", int: "Int64" if optional else "int64", bool: "boolean" if optional else "bool", date: "datetime64[s]"}
    if base not in dtypes:
        msg = f"no pandas dtype for {annotation!r}; add one in data._dtype"
        raise TypeError(msg)
    return dtypes[base]


def _cache_key(dataset: Dataset, params: Mapping[str, Param]) -> str:
    """Changes whenever the params, query or row schema change, so a deploy never reads a stale-shaped frame."""
    payload = json.dumps([json.dumps(dataset.row.model_json_schema(), sort_keys=True), dataset.query, params], sort_keys=True, default=str)
    return f"{dataset.name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
