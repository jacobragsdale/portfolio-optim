"""When the loader reads through its cache, and when it reads live."""

from datetime import timedelta

from optim.cache.memory import MemoryCache
from optim.datasets.accounts import ACCOUNTS
from optim.datasets.signals import SIGNALS
from tests.builders import AS_OF, account, loader, security, tables


def test_a_dataset_with_a_ttl_is_cached_until_it_expires() -> None:
    now = [0.0]
    load, source = loader(tables([security(1)]), MemoryCache(clock=lambda: now[0]))
    load.load(SIGNALS, as_of=AS_OF)
    load.load(SIGNALS, as_of=AS_OF)
    now[0] += timedelta(minutes=6).total_seconds()
    load.load(SIGNALS, as_of=AS_OF)
    assert source.calls == [SIGNALS.query] * 2


def test_a_dataset_without_a_ttl_is_always_read_live() -> None:
    load, source = loader(tables([security(1)], accounts=[account()]), MemoryCache())
    load.load(ACCOUNTS, accounts=("A1",))
    load.load(ACCOUNTS, accounts=("A1",))
    assert source.calls == [ACCOUNTS.query] * 2
