"""Batches load concurrently, never more at once than their source's limit, and the first failure stops the rest."""

import threading
import time
from collections.abc import Mapping

import pytest

from optim.data.loader import Dataset, Loader
from optim.schema import Row
from optim.sources import Columns, Param


class Item(Row):
    item_id: str


ITEMS = Dataset("items", Item, source="mssql", query="items", key=("item_id",))


class SlowSource:
    """Answers each batch with its own ids after a pause, or fails every batch."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail, self.calls, self.running, self.most = fail, 0, 0, 0
        self.lock = threading.Lock()

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:  # noqa: ARG002
        ids = params["ids"]
        assert isinstance(ids, tuple)
        with self.lock:
            self.calls += 1
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.05)
        with self.lock:
            self.running -= 1
        if self.fail:
            msg = "bad batch"
            raise RuntimeError(msg)
        return {"item_id": list(ids)}


def test_batches_respect_the_source_limit_and_keep_their_order() -> None:
    source = SlowSource()
    loader = Loader({"mssql": source}, limits={"mssql": threading.Semaphore(2)})

    frame = loader.load_batched(ITEMS, "ids", [f"i{n}" for n in range(10)], size=1)

    assert frame["item_id"].tolist() == [f"i{n}" for n in range(10)]
    assert source.most == 2


def test_a_failed_batch_skips_the_batches_still_waiting() -> None:
    source = SlowSource(fail=True)
    loader = Loader({"mssql": source}, limits={"mssql": threading.Semaphore(1)})

    with pytest.raises(RuntimeError, match="bad batch"):
        loader.load_batched(ITEMS, "ids", [f"i{n}" for n in range(10)], size=1)

    assert source.calls == 1
