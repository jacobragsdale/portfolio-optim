"""Loading boundary: what the row schemas refuse, and when the cache is used."""

import re
from datetime import datetime, timedelta

import pytest

from optim.cache import MemoryCache
from optim.data import to_frame
from optim.inputs import ACCOUNTS, SECURITIES, SIGNALS
from optim.sources import Columns
from tests.builders import AS_OF, Record, account, loader, security, tables


def columns(*records: Record) -> Columns:
    return {name: [record[name] for record in records] for name in records[0]}


def test_valid_rows_load_with_the_schema_dtypes() -> None:
    frame = to_frame(SECURITIES, columns(security(1, maturity=datetime(2033, 6, 1)), security(2, rating="A-", state=None, tax_status="taxable")))
    assert frame["rating"].cat.ordered
    assert (frame["rating"] <= "A-").all()
    assert {name: str(dtype) for name, dtype in frame.dtypes.items() if name in {"maturity", "state", "min_piece"}} == {"maturity": "datetime64[s]", "state": "str", "min_piece": "int64"}


@pytest.mark.parametrize(
    ("records", "error"),
    [
        pytest.param([security(1, price="100")], "securities.price row 0", id="wrong-type"),
        pytest.param([security(1), security(2, issuer=None)], "securities.issuer row 1", id="null-in-required"),
        pytest.param([security(1, rating="AAAA")], "securities.rating row 0", id="out-of-domain"),
        pytest.param([security(1, maturity=datetime(2033, 6, 1, 15, 30))], "securities.maturity row 0", id="timestamp-for-date"),
        pytest.param([security(1), security(1)], "securities: duplicate ('cusip',)", id="duplicate-key"),
        pytest.param([{k: v for k, v in security(1).items() if k != "duration"}], "securities: expected columns", id="missing-column"),
    ],
)
def test_bad_rows_are_rejected_with_their_location(records: list[Record], error: str) -> None:
    with pytest.raises(ValueError, match=re.escape(error)):
        to_frame(SECURITIES, columns(*records))


def test_a_dataset_with_a_ttl_is_cached_until_it_expires() -> None:
    now = [0.0]
    load, source = loader(tables([security(1)]), MemoryCache(clock=lambda: now[0]))
    load.load(SIGNALS, as_of=AS_OF)
    load.load(SIGNALS, as_of=AS_OF)
    now[0] += timedelta(minutes=6).total_seconds()
    load.load(SIGNALS, as_of=AS_OF)
    assert source.calls == ["/v1/expected-returns"] * 2


def test_a_dataset_without_a_ttl_is_always_read_live() -> None:
    load, source = loader(tables([security(1)], accounts=[account()]), MemoryCache())
    load.load(ACCOUNTS, accounts=("A1",))
    load.load(ACCOUNTS, accounts=("A1",))
    assert source.calls == ["accounts.sql"] * 2
