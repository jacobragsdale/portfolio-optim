"""Expected returns per security, from the signals API. They may be up to 5 minutes old."""

from datetime import timedelta

from optim.data.dataset import CachePolicy, Dataset
from optim.schema import Id, Row


class Signal(Row):
    security_id: Id
    expected_return: float
    """Expected annual return on market value, as a decimal."""


SIGNALS = Dataset("signals", Signal, source="signals", query="/v1/expected-returns", key=("security_id",), cache=CachePolicy(ttl=timedelta(minutes=5)))
