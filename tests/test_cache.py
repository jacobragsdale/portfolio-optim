"""A cache entry keeps its frame exactly, and when its source was read."""

import io
from datetime import UTC, datetime

import pytest
from builders import frame
from pandas.testing import assert_frame_equal

from optim.data.cache import decode, encode
from optim.datasets.securities import SECURITIES


def test_an_entry_gives_back_its_frame_and_fetch_time() -> None:
    securities, fetched_at = frame(SECURITIES, {}), datetime(2026, 10, 16, 13, 5, tzinfo=UTC)

    cached, when = decode(encode(securities, fetched_at))

    assert_frame_equal(cached, securities)  # dtypes included: the rating's ordered category, the maturity's ms dates
    assert when == fetched_at


def test_an_entry_older_code_wrote_without_a_fetch_time_is_refused() -> None:
    buffer = io.BytesIO()
    frame(SECURITIES, {}).to_parquet(buffer, index=False)

    with pytest.raises(ValueError, match="no fetch time"):
        decode(buffer.getvalue())
