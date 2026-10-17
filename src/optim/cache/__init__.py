"""Optional caching of loaded datasets. Each `Dataset.ttl` says whether and how long a dataset may be cached. A run
chooses the backend, or none: `Loader(sources, cache=None)` always reads live."""

from datetime import timedelta
from typing import Protocol

import pandas as pd


class Cache(Protocol):
    def get(self, key: str) -> pd.DataFrame | None: ...
    def set(self, key: str, frame: pd.DataFrame, ttl: timedelta) -> None: ...
