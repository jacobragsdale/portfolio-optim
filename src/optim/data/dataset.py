"""`Dataset`: one table's row schema, source, query, key and cache policy, declared in `optim.datasets` beside its row
model. A declaration only: importing one opens nothing, so prep and the optimizer can import the row models beside it.
The `Loader` fetches them."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from optim.data.frames import validate_columns
from optim.schema import Row
from optim.sources import Columns, Param, SourceName

if TYPE_CHECKING:  # a warm hook's argument: the loader itself is I/O, which a declaration must not import
    from optim.data.loader import Loader


@dataclass(frozen=True)
class CachePolicy:
    """How a dataset uses the cache. A dataset without one is read live on every load."""

    ttl: timedelta  # how long an entry stays fresh; every query param is in the key, so a new business date is a new entry
    warm: Callable[[Loader, date], Iterable[Mapping[str, Param]]] | None = None  # the params `optim warm` loads, one entry each
    live_on_miss: bool = True  # on a miss, or a stale entry, read the source; False fails the run instead

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            msg = f"cache ttl must be positive, got {self.ttl}"
            raise ValueError(msg)
        if not (self.live_on_miss or self.warm):
            msg = "live_on_miss=False needs a warm hook: nothing else would fill the cache"
            raise ValueError(msg)


@dataclass(frozen=True)
class Dataset:
    """One table: its row schema, where it comes from, its key, and how it is cached."""

    name: str
    row: type[Row]
    source: SourceName
    query: str  # a SQL file's path inside the optim package, or a REST path
    key: tuple[str, ...]  # columns that must be unique
    cache: CachePolicy | None = None  # None: always read live
    check: Callable[[pd.DataFrame], None] | None = None  # cross-row rules; raise ValueError

    def to_frame(self, columns: Columns) -> pd.DataFrame:
        """Raw columns to this dataset's validated, typed frame, with its key and cross-row rules checked."""
        frame = self.unique(validate_columns(self.row, columns, name=self.name, key=self.key))
        if self.check is not None:
            self.check(frame)
        return frame

    def unique(self, frame: pd.DataFrame, *, why: str = "") -> pd.DataFrame:
        """`frame`, once no two rows share a key."""
        if (duplicated := frame.duplicated(list(self.key))).any():
            examples = frame.loc[duplicated, list(self.key)].head(3).to_dict("records")
            msg = f"{self.name}: duplicate {self.key}{why}, e.g. {examples}"
            raise ValueError(msg)
        return frame
