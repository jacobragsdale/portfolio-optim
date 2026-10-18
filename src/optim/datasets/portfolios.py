"""Portfolios, their open tax lots and their recent loss sales, from SQL Server, which calls a portfolio an account. All
are read live: they change with every trade."""

from datetime import date

from optim.data.loader import Dataset
from optim.schema import Cusip, Par, Price, Rate, Row


class PortfolioDetails(Row):
    portfolio_id: str
    benchmark_id: str
    cash: float
    """Cash, $."""
    federal_rate: Rate
    """Marginal federal rate on ordinary income, such as interest."""
    state_rate: Rate
    """Marginal state rate on ordinary income."""
    st_gain_rate: Rate
    """Short-term capital gains rate, federal and state."""
    lt_gain_rate: Rate
    """Long-term capital gains rate, federal and state."""


class Lot(Row):
    portfolio_id: str
    lot_id: str
    cusip: Cusip
    par: Par
    cost_price: Price
    """Adjusted tax basis per 100 par."""
    acquired: date


class LossSale(Row):
    """A bond the portfolio sold at a loss in the 30 days to the business date."""

    portfolio_id: str
    cusip: Cusip
    sold: date


PORTFOLIOS = Dataset("portfolios", PortfolioDetails, source="mssql", query="datasets/sql/portfolios.sql", key=("portfolio_id",))
LOTS = Dataset("lots", Lot, source="mssql", query="datasets/sql/lots.sql", key=("portfolio_id", "lot_id"))
LOSS_SALES = Dataset("loss_sales", LossSale, source="mssql", query="datasets/sql/loss_sales.sql", key=("portfolio_id", "cusip", "sold"))
