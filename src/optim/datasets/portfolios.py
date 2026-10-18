"""Portfolios, from SQL Server, which calls a portfolio an account: each portfolio's details, its sleeves, what it holds
and the cash flows due. All are read live, a batch of portfolios at a time in one read-only transaction, so a batch's
holdings and cash are from the same moment (see `optim.inputs`)."""

from decimal import Decimal
from typing import Annotated

import pandas as pd
from pydantic import Field

from optim.data.loader import Dataset
from optim.schema import Currency, Id, Row, Weight


class PortfolioDetails(Row):
    portfolio_id: Id
    currency: Currency
    """Every value in the portfolio is in it. A security in another currency cannot be bought, and holding one fails
    the portfolio: there are no FX rates to value it."""
    target_value: Annotated[Decimal, Field(gt=0)] | None
    """The value the portfolio's weights are fractions of, as for a portfolio being funded to an agreed size; null: its
    value after cash flows."""


class Sleeve(Row):
    """A part of a portfolio with its own eligibility list and benchmark, such as a corporate or a Treasury sleeve. The
    sleeves of a portfolio share its cash and are solved together."""

    portfolio_id: Id
    sleeve_id: Id
    universe_id: Id | None
    """The eligibility list it buys from; null: it buys nothing."""
    benchmark_id: Id | None
    target_weight: Weight | None
    """Its share of the portfolio's base value, which makes its own base value; null: its value before trading."""


class Holding(Row):
    """A position: what one sleeve of a portfolio holds of one security, cash included."""

    portfolio_id: Id
    sleeve_id: Id
    security_id: Id
    quantity: Decimal
    """Face amount for a bond, currency units for cash. Cash may be negative, as when a purchase settles before the sale
    that funds it; any other holding must be positive (prep fails the portfolio otherwise)."""


class CashFlow(Row):
    portfolio_id: Id
    flow_id: Id
    amount: Decimal
    """In the portfolio's currency: positive for a contribution to invest, negative for a withdrawal to raise."""


def _targets_fit(frame: pd.DataFrame) -> None:
    totals = frame.groupby("portfolio_id")["target_weight"].sum()
    if not (over := totals[totals > 1 + 1e-9]).empty:
        msg = f"sleeves: a portfolio's target weights sum to at most 1: {over.head().to_dict()}"
        raise ValueError(msg)


PORTFOLIOS = Dataset("portfolios", PortfolioDetails, source="mssql", query="datasets/sql/portfolios.sql", key=("portfolio_id",))
SLEEVES = Dataset("sleeves", Sleeve, source="mssql", query="datasets/sql/sleeves.sql", key=("portfolio_id", "sleeve_id"), check=_targets_fit)
HOLDINGS = Dataset("holdings", Holding, source="mssql", query="datasets/sql/holdings.sql", key=("portfolio_id", "sleeve_id", "security_id"))
CASH_FLOWS = Dataset("cash_flows", CashFlow, source="mssql", query="datasets/sql/cash_flows.sql", key=("portfolio_id", "flow_id"))
