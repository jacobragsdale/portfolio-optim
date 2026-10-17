"""Datasets, and the `Loader` that fetches them through their sources, validates them and caches them."""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from itertools import batched

import pandas as pd

from optim.cache import Cache
from optim.data.frames import empty_frame, validate_columns
from optim.schema import Row
from optim.sources import Columns, Param, Source, SourceName
from optim.utils.timing import timed


@dataclass(frozen=True)
class Dataset:
    """One table: its row schema, where it comes from, its key, and how long it may be cached."""

    name: str
    row: type[Row]
    source: SourceName
    query: str  # a SQL file's path inside the optim package, or a REST path
    key: tuple[str, ...]  # columns that must be unique
    ttl: timedelta | None = None  # how long it may be cached; None means always read live
    check: Callable[[pd.DataFrame], None] | None = None  # cross-row rules; raise ValueError

    def to_frame(self, columns: Columns) -> pd.DataFrame:
        """Raw columns to this dataset's validated, typed frame, with its key and cross-row rules checked."""
        frame = validate_columns(self.row, columns, name=self.name)
        if (duplicated := frame.duplicated(list(self.key))).any():
            examples = frame.loc[duplicated, list(self.key)].head(3).to_dict("records")
            msg = f"{self.name}: duplicate {self.key}, e.g. {examples}"
            raise ValueError(msg)
        if self.check is not None:
            self.check(frame)
        return frame


@dataclass(frozen=True)
class Loader:
    """Fetches datasets through their sources, with an optional cache. Thread-safe."""

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

    def load_all(self, *datasets: Dataset, **params: Param) -> dict[str, pd.DataFrame]:
        """Load independent datasets concurrently, keyed by dataset name."""
        with ThreadPoolExecutor() as pool:
            futures = {dataset.name: pool.submit(self.load, dataset, **params) for dataset in datasets}
        return {name: future.result() for name, future in futures.items()}

    def load_batched(self, dataset: Dataset, param: str, ids: Sequence[str], *, size: int = 1000, **params: Param) -> pd.DataFrame:
        """Load `ids` in chunks of `size`, one query per chunk, binding each chunk to `param`."""
        frames = [self.load(dataset, **params, **{param: chunk}) for chunk in batched(ids, size, strict=False)]
        return pd.concat(frames, ignore_index=True) if frames else empty_frame(dataset.row)

    def _fetch(self, dataset: Dataset, params: Mapping[str, Param]) -> pd.DataFrame:
        with timed("load", dataset=dataset.name):
            return dataset.to_frame(self.sources[dataset.source].fetch(dataset.query, params))


def _cache_key(dataset: Dataset, params: Mapping[str, Param]) -> str:
    """Changes whenever the params, query or row schema change, so a deploy never reads a stale-shaped frame."""
    payload = json.dumps([json.dumps(dataset.row.model_json_schema(), sort_keys=True), dataset.query, params], sort_keys=True, default=str)
    return f"{dataset.name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
