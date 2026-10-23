"""The model: a taxable fixed income SMA rebalance, solved as an integer program. This file is its table of contents:
the params it takes, the data it reads, the numbers it reports, and its hooks, each a few lines that call `prep.py`,
the rules and the optimizer. The README beside it states what it does in business terms.

To add a param, add a field to `Params`, with a docstring under it. To read a new dataset, declare it in `datasets/` and
add it to `Data`. To add a rule, write it in `rules/` and call it in `prepare_portfolio`, in the order it applies."""

from dataclasses import dataclass

import optim
import optim.cvxpy
import pandas as pd
from optim.schema import Rating
from pydantic import Field, JsonValue

from optim.example import prep
from optim.example.datasets.controls import CONTROLS
from optim.example.datasets.mandates import MANDATES
from optim.example.datasets.portfolios import PORTFOLIOS
from optim.example.datasets.signals import SIGNALS
from optim.example.optimizer import solve
from optim.example.rules import controls, credit, mandates, screens

type Definition = dict[str, JsonValue]
"""A constraint or an objective term, as `optimizer/problem.py` reads it: the wrapper's vocabulary in a firm model."""


OBJECTIVE: list[Definition] = [{"term": "income"}, {"term": "cost", "bps": 5.0}]
"""What the model maximizes, unless the params say otherwise: see `optimizer/problem.py` for the terms."""


class Params(optim.Params):
    constraints: list[Definition] = Field(default_factory=list)
    """Limits every portfolio keeps, besides its own mandates: see `optimizer/problem.py` for the kinds."""
    objective: list[Definition] = Field(default_factory=lambda: list(OBJECTIVE))
    """What the model maximizes, a term at a time. A params file that sets it replaces the whole list."""
    min_rating: Rating = "A-"
    """Buy only bonds rated this or better, or better still where the portfolio's own floor is higher."""
    exclude_sectors: list[str] = Field(default_factory=list)
    """Never buy these sectors, and hold no bond of them the portfolio does not hold already."""
    exclude_issuers: list[str] = Field(default_factory=list)
    """Never buy these issuers, as a restricted list says."""
    solve: optim.cvxpy.SolveSettings = optim.cvxpy.SolveSettings()


class Data(optim.Datasets):
    portfolios = PORTFOLIOS
    signals = SIGNALS
    controls = CONTROLS
    mandates = MANDATES


class Metrics(optim.Metrics):
    income: float
    """Expected income over a year after trading, in the portfolio's currency."""
    cost: float
    """Estimated trading cost, in the portfolio's currency."""


@dataclass(frozen=True)
class Market:
    """What every portfolio shares."""

    securities: pd.DataFrame
    """Every security, with its `unit_value`, and its `expected_return`, 0 without a signal."""


@dataclass(frozen=True)
class Portfolio:
    """What one portfolio's decision sees: `prep.portfolio` builds it, and each rule returns it changed."""

    data: Data
    """The portfolio's own rows of each dataset, which rules read: `portfolio.data.controls`."""
    universe: pd.DataFrame
    """A row per security it holds, cash aside, or may buy, with `held`, and `buyable`, `sellable` and `must_sell`,
    which rules narrow."""
    constraints: list[Definition]
    objective: list[Definition]
    cash: float
    value: float
    """Cash and every holding at its unit value, from the holdings, so a row a rule drops still counts."""


def prepare_market(data: Data, params: Params) -> Market:
    return prep.market(data)


def prepare_portfolio(data: Data, market: Market, params: Params) -> Portfolio:
    portfolio = prep.portfolio(data, market, params)
    portfolio = controls.apply(portfolio, market, params)
    portfolio = mandates.constraints(portfolio, market, params)
    portfolio = credit.min_rating(portfolio, market, params)
    portfolio = screens.exclude_sectors(portfolio, market, params)
    return screens.exclude_issuers(portfolio, market, params)


def decide(portfolio: Portfolio, market: Market, params: Params) -> optim.Decision:
    return solve.solve(portfolio, params)
