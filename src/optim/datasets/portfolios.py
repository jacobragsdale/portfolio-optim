"""Portfolios, their open tax lots and their recent loss sales, from SQL Server, which calls a portfolio an account. All
are read live: they change with every trade."""

from datetime import date

from optim.data.loader import Dataset
from optim.schema import Cusip, Par, Price, Rate, Row


class PortfolioDetails(Row):
    portfolio_id: str
    benchmark_id: str
    cash: float
    federal_rate: Rate  # marginal rate on ordinary income
    state_rate: Rate
    st_gain_rate: Rate  # short-term capital gains, federal + state
    lt_gain_rate: Rate  # long-term capital gains, federal + state


class Lot(Row):
    portfolio_id: str
    lot_id: str
    cusip: Cusip
    par: Par
    cost_price: Price  # adjusted tax basis
    acquired: date


class LossSale(Row):
    """A bond the portfolio sold at a loss in the 30 days to the business date."""

    portfolio_id: str
    cusip: Cusip
    sold: date


PORTFOLIOS = Dataset("portfolios", PortfolioDetails, source="mssql", query="datasets/sql/portfolios.sql", key=("portfolio_id",))
LOTS = Dataset("lots", Lot, source="mssql", query="datasets/sql/lots.sql", key=("portfolio_id", "lot_id"))
LOSS_SALES = Dataset("loss_sales", LossSale, source="mssql", query="datasets/sql/loss_sales.sql", key=("portfolio_id", "cusip", "sold"))
