"""Expected returns per bond, from the signals API. They may be up to 5 minutes old."""

from datetime import timedelta

from optim.data.loader import CachePolicy, Dataset
from optim.schema import Cusip, Row


class Signal(Row):
    cusip: Cusip
    expected_return: float  # annual, pre-tax, decimal


SIGNALS = Dataset("signals", Signal, source="signals", query="/v1/expected-returns", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=5)))
