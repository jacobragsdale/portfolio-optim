"""Oracle through python-oracledb in thin mode, which needs no client libraries. Queries bind `:name`."""

from collections.abc import Generator
from contextlib import contextmanager
from functools import partial

import oracledb
from pydantic import SecretStr

from optim.schema import Strict
from optim.sources import TIMEOUT_S
from optim.sources.sql import Sql


class OracleSettings(Strict):
    dsn: str
    user: str
    password: SecretStr


@contextmanager
def connect(settings: OracleSettings) -> Generator[Sql]:
    """A pooled source. The pool closes on exit."""
    pool = oracledb.create_pool(user=settings.user, password=settings.password.get_secret_value(), dsn=settings.dsn, min=1, max=8)
    try:
        yield Sql(partial(_acquire, pool))
    finally:
        pool.close()


def _acquire(pool: oracledb.ConnectionPool) -> oracledb.Connection:
    connection = pool.acquire()
    connection.call_timeout = TIMEOUT_S * 1000  # milliseconds, per round trip
    return connection
