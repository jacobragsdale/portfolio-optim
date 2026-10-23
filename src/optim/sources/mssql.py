"""SQL Server through mssql-python, Microsoft's DB-API driver. Queries bind `%(name)s`. DECIMAL columns arrive as exact
`Decimal`s."""

from functools import partial

import mssql_python
from pydantic import SecretStr

from optim.sources import SourceSettings
from optim.sources.sql import SqlSource

LOGIN_TIMEOUT_S = 30
# The driver runs with autocommit off, so the first query begins the transaction, and every query in it reads the row
# versions as of that moment. The database must allow it (`ALTER DATABASE ... SET ALLOW_SNAPSHOT_ISOLATION ON`); where it
# does not, the first read-only transaction fails and says so. Connect to the primary, not an asynchronous readable
# secondary (`ApplicationIntent=ReadOnly`): working holdings must include the trades just booked.
BEGIN_READ_ONLY = "SET TRANSACTION ISOLATION LEVEL SNAPSHOT"


class MssqlSettings(SourceSettings):
    connection_string: SecretStr


def connect(settings: MssqlSettings) -> SqlSource:
    return SqlSource(partial(_connect, settings.connection_string.get_secret_value(), settings.timeout_s), BEGIN_READ_ONLY, settings.max_rows)


def _connect(connection_string: str, timeout_s: int) -> mssql_python.Connection:
    try:
        connection = mssql_python.connect(connection_string, timeout=LOGIN_TIMEOUT_S)
    except mssql_python.Error as error:
        # The driver names neither the server nor the database, and reports a missing database as a failed login.
        error.add_note(f"while connecting to SQL Server {_target(connection_string)}")
        raise
    connection.timeout = timeout_s  # seconds, per statement
    return connection


def _target(connection_string: str) -> str:
    """The server and database a connection string names, for errors: never its credentials."""
    parts = {key.strip().lower(): value.strip() for key, _, value in (part.partition("=") for part in connection_string.split(";"))}
    return f"{parts.get('server', '?')}, database {parts.get('database', '?')}"
