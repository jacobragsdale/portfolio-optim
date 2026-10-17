"""SQL Server through mssql-python, Microsoft's DB-API driver. Queries bind `%(name)s`."""

from functools import partial

import mssql_python
from pydantic import SecretStr

from optim.schema import Strict
from optim.sources import TIMEOUT_S
from optim.sources.sql import Sql

LOGIN_TIMEOUT_S = 30


class MssqlSettings(Strict):
    connection_string: SecretStr


def connect(settings: MssqlSettings) -> Sql:
    return Sql(partial(_connect, settings.connection_string.get_secret_value()))


def _connect(connection_string: str) -> mssql_python.Connection:
    connection = mssql_python.connect(connection_string, timeout=LOGIN_TIMEOUT_S)
    connection.timeout = TIMEOUT_S  # seconds, per statement
    return connection
