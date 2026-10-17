"""Row schemas at the loading boundary: what they accept, and what they refuse."""

import re
from datetime import datetime

import pytest

from optim.datasets.securities import SECURITIES
from optim.sources import Columns, columns_from_records
from tests.builders import Record, security


def columns(*records: Record) -> Columns:
    return columns_from_records(records, source="test")


def test_valid_rows_load_with_the_schema_dtypes() -> None:
    frame = SECURITIES.to_frame(columns(security(1, maturity=datetime(2033, 6, 1)), security(2, rating="A-", state=None, tax_status="taxable")))
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
        SECURITIES.to_frame(columns(*records))
