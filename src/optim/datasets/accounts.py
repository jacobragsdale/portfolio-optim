"""Accounts and their open tax lots, from SQL Server. Both are read live: holdings change with every trade."""

from optim.data.loader import Dataset
from optim.schema import Cusip, Date, Par, Price, Rate, Row, State


class Account(Row):
    account_id: str
    benchmark_id: str
    cash: float
    state: State | None  # tax residence; None when no state income tax applies
    federal_rate: Rate  # marginal rate on ordinary income
    state_rate: Rate
    st_gain_rate: Rate  # short-term capital gains, federal + state
    lt_gain_rate: Rate  # long-term capital gains, federal + state


class Lot(Row):
    account_id: str
    lot_id: str
    cusip: Cusip
    par: Par
    cost_price: Price  # adjusted tax basis
    acquired: Date


ACCOUNTS = Dataset("accounts", Account, source="mssql", query="datasets/sql/accounts.sql", key=("account_id",))
LOTS = Dataset("lots", Lot, source="mssql", query="datasets/sql/lots.sql", key=("account_id", "lot_id"))
