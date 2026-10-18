"""Portfolios and their open tax lots, from SQL Server, which calls a portfolio an account. Both are read live: they
change with every trade."""

from datetime import date

from optim.data.loader import Dataset
from optim.schema import Cusip, Par, Price, Rate, Row


class PortfolioDetails(Row):
    portfolio_id: str
    benchmark_id: str
    cash: float
    """Cash, $."""
    st_gain_rate: Rate
    """Short-term capital gains rate."""
    lt_gain_rate: Rate
    """Long-term capital gains rate."""


class Lot(Row):
    portfolio_id: str
    lot_id: str
    cusip: Cusip
    par: Par
    cost_price: Price
    """Tax basis per 100 par."""
    acquired: date


PORTFOLIOS = Dataset("portfolios", PortfolioDetails, source="mssql", query="datasets/sql/portfolios.sql", key=("portfolio_id",))
LOTS = Dataset("lots", Lot, source="mssql", query="datasets/sql/lots.sql", key=("portfolio_id", "lot_id"))
