"""The cvxpy example: a taxable fixed income SMA rebalance, solved as an integer program. It maximizes each portfolio's
expected income net of trading costs, trading whole increments of each bond, within position, issuer and turnover
limits and the portfolio's own controls. The README beside this module states its behaviour in business terms."""

from dataclasses import dataclass

import cvxpy as cp
import numpy as np
import optim
import optim.cvxpy
import pandas as pd
import scipy.sparse as sp
from optim.schema import Rating
from pydantic import Field

from optim.examples.cvxpy import rules
from optim.examples.cvxpy.datasets import CONTROLS, SIGNALS


class Params(optim.Params):
    max_weight: float = Field(default=0.05, gt=0, le=1)
    """The most of the portfolio's value in any one security after trading, unless it may not be sold."""
    max_issuer_weight: float = Field(default=0.10, gt=0, le=1)
    """The most of the portfolio's value in any one issuer after trading."""
    max_turnover: float = Field(default=0.25, ge=0)
    """The most the portfolio may buy plus sell, as a fraction of its value."""
    min_rating: Rating = "A-"
    """Buy only bonds rated this or better."""
    exclude_issuers: list[str] = Field(default_factory=list)
    """Never buy these issuers, as a restricted list says."""
    cost_bps: float = Field(default=5.0, ge=0)
    """Estimated one-way trading cost, in basis points of value traded."""
    solve: optim.cvxpy.SolveSettings = optim.cvxpy.SolveSettings()


class Data(optim.Datasets):
    signals = SIGNALS
    controls = CONTROLS


class Metrics(optim.Metrics):
    income: float
    """Expected income over a year after trading, in the portfolio's currency."""
    cost: float
    """Estimated trading cost, in the portfolio's currency."""


@dataclass(frozen=True)
class Market:
    """What every portfolio shares: each security with its unit value and expected return."""

    securities: pd.DataFrame


@dataclass(frozen=True)
class Portfolio:
    """What one portfolio's decision sees, after its controls and the params' rules."""

    lines: pd.DataFrame
    """One per security it holds, cash aside, or may buy: see `rules.lines`."""
    cash: float
    value: float
    """Cash and every holding at its unit value: what weights are fractions of."""
    params: Params
    """The limits `check` holds the decision to."""


def prepare(data: Data, params: Params) -> Market:  # noqa: ARG001 -- the hook's signature
    return Market(rules.market(data.securities, data.signals))


def prepare_portfolio(portfolio: Data, shared: Market, params: Params) -> Portfolio:
    lines = rules.lines(portfolio.holdings, shared.securities)
    lines = rules.apply_controls(lines, portfolio.controls)
    lines = rules.min_rating(lines, params.min_rating)
    lines = rules.exclude_issuers(lines, params.exclude_issuers)
    cash = rules.cash(portfolio.holdings, shared.securities)
    return Portfolio(lines, cash, cash + float((lines["unit_value"] * lines["held"]).sum()), params)


def decide(prepared: Portfolio, shared: Market, params: Params) -> optim.Decision:  # noqa: ARG001 -- the hook's signature
    """Buys are nothing or whole increments of at least the minimum piece. A sale is the whole holding, which is how an
    odd lot goes and how a mandatory sale must go, or whole increments of at least the minimum piece that leave at least
    the minimum remaining. A bond is bought or sold, never both: selling it to buy it back is a wash sale, and two
    trades that dodge the sizes the market trades."""
    lines, value = prepared.lines, prepared.value
    held, unit, increment = (lines[column].to_numpy(dtype=float) for column in ("held", "unit_value", "increment"))
    min_piece = lines["min_piece"].fillna(lines["increment"]).to_numpy(dtype=float)
    min_remaining = lines["min_remaining"].fillna(0.0).to_numpy(dtype=float)
    buyable, sellable, must_sell = (lines[column].to_numpy(dtype=bool) for column in ("buyable", "sellable", "must_sell"))

    n = len(lines)
    buy_steps, buying = cp.Variable(n, integer=True), cp.Variable(n, boolean=True)
    sell_steps, part, whole = cp.Variable(n, integer=True), cp.Variable(n, boolean=True), cp.Variable(n, boolean=True)
    buy, partial = cp.multiply(increment, buy_steps), cp.multiply(increment, sell_steps)
    sell = cp.multiply(held, whole) + partial
    after = held + buy - sell
    cost = (unit * params.cost_bps / 1e4) @ (buy + sell)
    income = (lines["expected_return"].to_numpy(dtype=float) * unit) @ after
    weights = cp.multiply(unit / value, after)
    cap = np.maximum(params.max_weight, rules.kept(lines, value).to_numpy())  # what it may not sell, it may keep
    issuers = pd.get_dummies(lines["issuer"])
    issuer_caps = rules.issuer_caps(lines, value, params.max_issuer_weight).reindex(issuers.columns).to_numpy(dtype=float)
    constraints = [
        buy_steps >= 0,
        buy >= cp.multiply(min_piece, buying),
        buy <= cp.multiply(np.where(buyable, value / unit, 0.0), buying),
        sell_steps >= 0,
        partial >= cp.multiply(min_piece, part),
        partial <= cp.multiply(np.where(sellable, np.maximum(held - min_remaining, 0.0), 0.0), part),
        whole + part <= sellable.astype(float),
        whole >= must_sell.astype(float),
        buying + whole + part <= 1,
        prepared.cash + unit @ (sell - buy) - cost >= 0,
        weights <= cap,
        sp.csr_array(issuers.to_numpy(dtype=float).T) @ weights <= issuer_caps,
        (unit / value) @ (buy + sell) <= params.max_turnover,
    ]
    problem = cp.Problem(cp.Maximize((income - cost) / value), constraints)
    return optim.cvxpy.solve(problem, params.solve, securities=lines["security_id"].tolist(), buy=buy, sell=sell, metrics={"income": income, "cost": cost})


def check(prepared: Portfolio, decision: optim.Decision) -> list[str]:
    """The model's own acceptance, at the rounded trades: sizes the market trades, the controls, and the limits, which
    the core cannot see inside the cvxpy problem."""
    params = prepared.params
    return rules.violations(prepared.lines, prepared.value, decision, max_weight=params.max_weight, max_issuer_weight=params.max_issuer_weight, max_turnover=params.max_turnover)
