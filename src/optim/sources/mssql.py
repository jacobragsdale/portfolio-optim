"""SQL Server through mssql-python, Microsoft's DB-API driver. Queries bind `%(name)s`."""

import uuid
from functools import partial

import mssql_python
from pydantic import SecretStr

from optim.schema import Model
from optim.sources import TIMEOUT_S
from optim.sources.sql import Sql

LOGIN_TIMEOUT_S = 30


class MssqlSettings(Model):
    connection_string: SecretStr


def connect(settings: MssqlSettings) -> Sql:
    return Sql(partial(_connect, settings.connection_string.get_secret_value()))


def new_run_id() -> str:
    """Register a run in the runs table and return its id."""
    # ponytail: a mock until the runs table is wired in. The real one takes the connection and inserts, so it must commit:
    # mssql-python defaults to autocommit=False, and `Sql.fetch` never commits.
    return str(uuid.uuid7())


def _connect(connection_string: str) -> mssql_python.Connection:
    try:
        connection = mssql_python.connect(connection_string, timeout=LOGIN_TIMEOUT_S)
    except mssql_python.Error as error:
        # The driver names neither the server nor the database, and reports a missing database as a failed login.
        error.add_note(f"while connecting to SQL Server {_target(connection_string)}")
        raise
    connection.timeout = TIMEOUT_S  # seconds, per statement
    return connection


def _target(connection_string: str) -> str:
    """The server and database a connection string names, for errors: never its credentials."""
    parts = {key.strip().lower(): value.strip() for key, _, value in (part.partition("=") for part in connection_string.split(";"))}
    return f"{parts.get('server', '?')}, database {parts.get('database', '?')}"
