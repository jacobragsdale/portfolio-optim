"""Oracle through python-oracledb in thin mode, which needs no client libraries. Queries bind `:name`."""

import threading
from collections.abc import Callable
from concurrent.futures import Future, wait
from functools import partial

import oracledb
from pydantic import SecretStr

from optim.sources import SourceSettings
from optim.sources.sql import SqlSource

CONNECT_TIMEOUT_S = 30


class OracleSettings(SourceSettings):
    dsn: str
    user: str
    password: SecretStr


def connect(settings: OracleSettings) -> SqlSource:
    """A connection per query, as for SQL Server: a run makes a handful. Not a pool: its background thread joins a
    connection attempt that never ends, so a hung database would keep the process from exiting."""
    return SqlSource(partial(_connect, settings))


def _connect(settings: OracleSettings) -> oracledb.Connection:
    """A connection with a deadline on every round trip. Thin mode bounds only the TCP connect: a database that accepts
    TCP but never answers the login would hang the run, so the login runs in a thread that the run stops waiting for."""
    login = partial(oracledb.connect, user=settings.user, password=settings.password.get_secret_value(), dsn=settings.dsn)
    connected: Future[oracledb.Connection] = Future()
    threading.Thread(target=_resolve, args=(connected, login), daemon=True).start()
    if not wait([connected], timeout=CONNECT_TIMEOUT_S).done:
        msg = f"Oracle at {settings.dsn} accepted a connection but did not log in within {CONNECT_TIMEOUT_S}s: the listener or the database is hung"
        raise TimeoutError(msg)
    connection = connected.result()
    connection.call_timeout = settings.timeout_s * 1000  # milliseconds, per round trip
    return connection


def _resolve[T](future: Future[T], call: Callable[[], T]) -> None:
    try:
        future.set_result(call())
    except Exception as error:  # noqa: BLE001 -- handed to the waiting caller, which raises it
        future.set_exception(error)
