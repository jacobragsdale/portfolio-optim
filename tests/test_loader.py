"""Loads run concurrently, never more at once than their source's limit, and the first failure stops the rest. A
read-only scope reads one moment of its source, on one connection its loads take turns on. Rows arrive bounded, and
booked values exact."""

import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import oracledb
import pytest
from builders import CASH, frame

from optim.data.dataset import Dataset
from optim.data.loader import Loader, TaskGroup
from optim.datasets.portfolios import HOLDINGS
from optim.datasets.securities import SECURITIES
from optim.schema import Row
from optim.sources import Columns, Param, TooManyRowsError, oracle
from optim.sources.sql import SqlSource


class Item(Row):
    item_id: str


ITEMS = Dataset("items", Item, source="mssql", query="datasets/sql/portfolios.sql", key=("item_id",))  # a real file: SqlSource reads it


class Database:
    """A DB-API driver over `rows`, recording each connection, and how many were open at once."""

    def __init__(self, rows: Sequence[str] = ("i1", "i2")) -> None:
        self.rows, self.connections, self.open, self.most, self.dead = rows, list[Connection](), 0, 0, False
        self.lock = threading.Lock()

    def connect(self) -> Connection:
        with self.lock:
            self.open += 1
            self.most = max(self.most, self.open)
            self.connections.append(connection := Connection(self))
        return connection


class Connection:
    def __init__(self, database: Database) -> None:
        self.database, self.statements, self.rolled_back, self.closed, self.busy = database, list[str](), False, False, False

    def cursor(self) -> Cursor:
        return Cursor(self)

    def rollback(self) -> None:
        self.rolled_back = True
        if self.database.dead:
            msg = "DPY-4011: the database or network closed the connection"
            raise ConnectionError(msg)

    def close(self) -> None:
        self.closed = True
        with self.database.lock:
            self.database.open -= 1


class Cursor:
    arraysize = 1
    description = (("ITEM_ID",),)

    def __init__(self, connection: Connection) -> None:
        self.connection, self.left = connection, [(row,) for row in connection.database.rows]

    def execute(self, statement: str, parameters: dict[str, object] | None = None, /) -> None:  # noqa: ARG002
        assert not self.connection.busy, "two threads used one connection at once"
        self.connection.busy = True
        self.connection.statements.append(statement)
        time.sleep(0.02)
        self.connection.busy = False

    def fetchmany(self, size: int = 1, /) -> list[tuple[str]]:
        batch, self.left = self.left[:size], self.left[size:]
        return batch

    def close(self) -> None:
        pass


def test_loads_in_a_read_only_scope_share_one_transaction_and_take_turns() -> None:
    database = Database()
    loader = Loader({"mssql": SqlSource(database.connect, "SET TRANSACTION READ ONLY", max_rows=100)})

    with loader.read_only("mssql") as source, TaskGroup() as group:
        loads = [group.submit(source.load, ITEMS, batch=n) for n in range(4)]

    assert all(load.result()["item_id"].tolist() == ["i1", "i2"] for load in loads)
    [connection] = database.connections
    assert connection.statements[0] == "SET TRANSACTION READ ONLY"
    assert len(connection.statements) == 5  # begun, then each load
    assert connection.rolled_back
    assert connection.closed


def test_a_transaction_that_cannot_roll_back_still_raises_the_error_that_ended_it() -> None:
    database = Database()
    database.dead = True  # as after a call timeout
    loader = Loader({"mssql": SqlSource(database.connect, "SET TRANSACTION READ ONLY", max_rows=100)})

    def time_out() -> None:
        with loader.read_only("mssql"):
            msg = "DPY-4024: call timeout of 240000 ms exceeded"
            raise TimeoutError(msg)

    with pytest.raises(TimeoutError, match="call timeout"):
        time_out()

    assert database.connections[0].closed


def test_a_read_only_scope_holds_one_of_its_sources_slots_throughout() -> None:
    database = Database()
    loader = Loader({"mssql": SqlSource(database.connect, "SET TRANSACTION READ ONLY", max_rows=100)}, limits={"mssql": threading.Semaphore(1)})

    def two_reads() -> None:
        with loader.read_only("mssql") as source:
            source.load(ITEMS)
            source.load(ITEMS)

    with TaskGroup() as group:
        for _ in range(3):
            group.submit(two_reads)

    assert len(database.connections) == 3
    assert database.most == 1


class Failing:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:  # noqa: ARG002
        self.calls += 1
        time.sleep(0.05)
        msg = "bad batch"
        raise RuntimeError(msg)

    def read_only(self) -> nullcontext[Failing]:
        return nullcontext(self)


def test_a_failed_load_skips_the_loads_still_waiting() -> None:
    source = Failing()
    loader = Loader({"mssql": source}, limits={"mssql": threading.Semaphore(1)})

    def ten_loads() -> None:
        with TaskGroup() as group:
            for n in range(10):
                group.submit(loader.load, ITEMS, batch=n)

    with pytest.raises(RuntimeError, match="bad batch"):
        ten_loads()

    assert source.calls == 1


def test_a_query_that_returns_more_than_max_rows_fails() -> None:
    loader = Loader({"mssql": SqlSource(Database(rows=[f"i{n}" for n in range(11)]).connect, "", max_rows=10)})

    with pytest.raises(TooManyRowsError, match="more than 10 rows"):
        loader.load(ITEMS)


def test_booked_values_stay_exact_and_a_denomination_that_is_not_whole_fails() -> None:
    holdings = frame(HOLDINGS, {"quantity": Decimal("1234567.891")})

    assert holdings["quantity"].tolist() == [Decimal("1234567.891")]
    assert frame(SECURITIES, {"min_piece": Decimal("1000.000")})["min_piece"].tolist() == [1000]
    with pytest.raises(ValueError, match=r"securities.min_piece at security_id 'AAAAAAAA1'"):
        frame(SECURITIES, {"min_piece": Decimal("1000.5")})
    with pytest.raises(ValueError, match="all set or all null"):
        frame(SECURITIES, CASH | {"increment": 1000})


def oracle_settings(mode: str, client_lib_dir: Path) -> oracle.OracleSettings:
    return oracle.OracleSettings.model_validate({"dsn": "db:1521/X", "user": "u", "password": "p", "mode": mode, "client_lib_dir": client_lib_dir, "max_concurrent": 1, "timeout_s": 1, "max_rows": 10})


def test_oracle_in_thick_mode_loads_its_client_libraries_from_the_directory_given(tmp_path: Path) -> None:
    with pytest.raises(oracledb.DatabaseError, match=f"DPI-1047: Cannot locate .*{tmp_path}"):  # an empty directory: no client there
        oracle.connect(oracle_settings("thick", tmp_path))


def test_oracle_in_thin_mode_needs_no_client_and_fetches_exact_decimals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oracledb.defaults, "fetch_decimals", False)  # restored after the test: it is process-wide

    oracle.connect(oracle_settings("thin", tmp_path))

    assert oracledb.defaults.fetch_decimals
