"""The optimization model: one LP per portfolio, written as numpy/scipy arrays over two cvxpy variables.

Variables are par ($ face): `buy` per security and `sell` per tax lot, so the solver chooses which lots to sell
(specific identification, where most of the tax savings are). Constraints are in weights, fractions of pre-trade value,
so they share one scale and one feasibility tolerance. Summary numbers are dollars.

To add a constraint: write a class with `build(model) -> list[cp.Constraint]` and add it to `Constraint`.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydantic import Field

from optim.data import Strict
from optim.inputs import Inputs, Weight

TOLERANCE = 1e-6  # in weights: $1 per $1mm of portfolio value


@dataclass(frozen=True)
class Model:
    """The variables, and the expressions that objectives and constraints are written against."""

    securities: pd.DataFrame  # in variable order
    lots: pd.DataFrame  # in variable order
    lot_rows: np.ndarray  # the securities row of each lot
    buy: cp.Variable  # par bought, per security
    sell: cp.Variable  # par sold, per lot
    value: float  # pre-trade value including cash: the fixed denominator of every weight
    par: cp.Expression  # post-trade par, per security
    weights: cp.Expression  # post-trade weight, per security
    target: np.ndarray  # target weight, per security
    target_duration: float  # of the whole target, including bonds outside the model
    untracked: float  # target weight outside the model (neither held nor buyable): a constant part of tracking error
    cash: cp.Expression  # post-trade cash, $
    turnover: cp.Expression  # value traded, $
    tax: cp.Expression  # tax on realized gains, $; negative when harvesting losses
    cost: cp.Expression  # trading costs, $
    income: cp.Expression  # expected after-tax return, $ per year
    bounds: list[cp.Constraint]  # what every solve needs: offer sizes, lot sizes, no overspending


def build(inputs: Inputs) -> Model:
    s, lots, account = inputs.securities, inputs.lots, inputs.account
    # Variables only for what can be bought or is held. A target bond outside that set is a constant in tracking
    # error, so the model shrinks from the whole benchmark to the tradable set without changing the answer.
    in_model = (s["buyable"] | s["cusip"].isin(lots["cusip"])).to_numpy()
    target = inputs.target.reindex(s["cusip"], fill_value=0.0).to_numpy()
    sec = s.loc[in_model].reset_index(drop=True)
    lot_rows = pd.Index(sec["cusip"]).get_indexer(pd.Index(lots["cusip"]))
    if (lot_rows < 0).any():
        msg = f"account {account.account_id} holds bonds with no analytics: {lots.loc[lot_rows < 0, 'cusip'].head().tolist()}"
        raise LookupError(msg)

    n, k = len(sec), len(lots)
    holds = sp.csr_array((np.ones(k), (lot_rows, np.arange(k))), shape=(n, k))  # security x lot
    price = sec["price"].to_numpy() / 100  # $ per $1 par
    lot_par = lots["par"].to_numpy(dtype=float)
    value = float(account.cash + price @ (holds @ lot_par))
    if value <= 0:
        msg = f"account {account.account_id} has nothing to invest"
        raise ValueError(msg)

    buy = cp.Variable(n, nonneg=True, name="buy")
    sell = cp.Variable(k, nonneg=True, name="sell")
    sold = holds @ sell
    par = holds @ lot_par + buy - sold
    cost = (price * sec["cost_bps"].to_numpy() / 1e4) @ (buy + sold)
    cash = account.cash + price @ (sold - buy) - cost
    gain = price[lot_rows] - lots["cost_price"].to_numpy() / 100  # per $1 par
    long_term = (lots["acquired"] + pd.DateOffset(years=1) < pd.Timestamp(inputs.as_of)).to_numpy()  # held over a year
    offer = np.where(sec["buyable"], sec["offer_par"].fillna(0), 0)
    return Model(
        securities=sec,
        lots=lots,
        lot_rows=lot_rows,
        buy=buy,
        sell=sell,
        value=value,
        par=par,
        weights=cp.multiply(price, par) / value,
        target=target[in_model],
        target_duration=float(target @ s["duration"].to_numpy()),
        untracked=float(target[~in_model].sum()),
        cash=cash,
        turnover=price @ (buy + sold),
        tax=(np.where(long_term, account.lt_gain_rate, account.st_gain_rate) * gain) @ sell,
        cost=cost,
        income=(sec["expected_return"].to_numpy() * price) @ par,
        bounds=[buy <= offer, sell <= lot_par, cash / value >= 0],
    )


class Objective(Strict):
    """Maximize after-tax expected return over the horizon, net of taxes and trading costs, less a penalty on distance
    from the target. Measured as a fraction of portfolio value."""

    horizon_years: float = Field(default=1.0, gt=0)
    tax_aversion: float = Field(default=1.0, ge=0)  # 1: a dollar of tax costs a dollar of return
    tracking_penalty: float = Field(default=0.0, ge=0)  # per unit of L1 distance between weights and target

    def expression(self, m: Model) -> cp.Expression:
        tracking = cp.norm1(m.weights - m.target) if self.tracking_penalty else 0.0  # L1 adds a variable and two rows per security
        net: cp.Expression = (self.horizon_years * m.income - self.tax_aversion * m.tax - m.cost) / m.value - self.tracking_penalty * tracking
        return net


class PositionLimit(Strict):
    """No bond above `max_weight` of the portfolio."""

    kind: Literal["position_limit"] = "position_limit"
    max_weight: Weight

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.weights <= self.max_weight]


class GroupLimit(Strict):
    """Cap the weight of each issuer, sector, state or rating. `overrides` sets the cap for named groups."""

    kind: Literal["group_limit"] = "group_limit"
    by: Literal["issuer", "sector", "state", "rating"]
    max_weight: Weight
    overrides: dict[str, Weight] = Field(default_factory=dict)

    def build(self, m: Model) -> list[cp.Constraint]:
        groups, members = indicator(m.securities[self.by])
        if not len(groups):
            return []
        caps = np.array([self.overrides.get(str(group), self.max_weight) for group in groups])
        return [members @ m.weights <= caps]


class DurationBand(Strict):
    """Keep portfolio duration within `tolerance` years of the target's."""

    kind: Literal["duration_band"] = "duration_band"
    tolerance: float = Field(gt=0)

    def build(self, m: Model) -> list[cp.Constraint]:
        gap = m.securities["duration"].to_numpy() @ m.weights - m.target_duration
        return [gap <= self.tolerance, gap >= -self.tolerance]


class MaxTurnover(Strict):
    """Trade at most `max_weight` of the portfolio's value."""

    kind: Literal["max_turnover"] = "max_turnover"
    max_weight: Weight

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.turnover / m.value <= self.max_weight]


class CashRange(Strict):
    """Keep cash between `min_weight` and `max_weight` of the portfolio."""

    kind: Literal["cash_range"] = "cash_range"
    min_weight: Weight = 0.0
    max_weight: Weight = 1.0

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.cash / m.value >= self.min_weight, m.cash / m.value <= self.max_weight]


class MaxTax(Strict):
    """Realize at most `dollars` of tax."""

    kind: Literal["max_tax"] = "max_tax"
    dollars: float = Field(ge=0)

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.tax / m.value <= self.dollars / m.value]


Constraint = Annotated[PositionLimit | GroupLimit | DurationBand | MaxTurnover | CashRange | MaxTax, Field(discriminator="kind")]


def indicator(labels: pd.Series) -> tuple[pd.Index, sp.csr_array]:
    """The distinct labels, and a sparse groups x rows 0/1 membership matrix. Missing labels belong to no group."""
    codes, groups = pd.factorize(labels)
    rows = np.flatnonzero(codes >= 0)
    return pd.Index(groups), sp.csr_array((np.ones(len(rows)), (codes[rows], rows)), shape=(len(groups), len(labels)))


@dataclass(frozen=True)
class Solved:
    account_id: str
    strategy: str
    trades: pd.DataFrame  # account_id, cusip, lot_id (sells only), side, par
    positions: pd.DataFrame  # cusip, par, weight after trading
    summary: dict[str, float]  # objective; value, cash, turnover, tax, cost, income in $; tracking as L1 weight distance
    violations: dict[str, float]  # constraint -> worst excess in weights, after rounding to tradable lots
    seconds: float

    @property
    def ok(self) -> bool:
        """Safe to trade: rounding broke no constraint."""
        return not self.violations


@dataclass(frozen=True)
class Unsolved:
    account_id: str
    strategy: str
    status: str  # the solver's status ("infeasible", ...) or the error that stopped the solve


type Result = Solved | Unsolved


def solve(inputs: Inputs, objective: Objective, constraints: Sequence[Constraint], *, strategy: str) -> Result:
    start = time.perf_counter()
    account_id = inputs.account.account_id
    m = build(inputs)
    named = {"bounds": m.bounds} | {f"{i}:{c.kind}": c.build(m) for i, c in enumerate(constraints)}
    problem = cp.Problem(cp.Maximize(objective.expression(m)), [c for group in named.values() for c in group])
    problem.solve(solver=cp.HIGHS, warm_start=False)  # warm starts made HiGHS re-solves ~6x slower when measured
    if problem.status != cp.OPTIMAL:
        return Unsolved(account_id, strategy, str(problem.status))

    # The LP trades fractional par; the market trades lots. Round toward cash (buys down, sells up), which keeps
    # limits and the cash floor intact, then re-check every constraint at the rounded trades: turnover, tax and
    # duration can still move past their bounds.
    # ponytail: rounding heuristic; add a repair re-solve with fixed lots if violations turn out to be common
    increment, min_piece = m.securities["increment"].to_numpy(), m.securities["min_piece"].to_numpy()
    m.buy.value = _round_buys(_value(m.buy), increment, min_piece)
    m.sell.value = _round_sells(_value(m.sell), increment[m.lot_rows], min_piece[m.lot_rows], m.lots["par"].to_numpy(dtype=float))
    violations = {name: worst for name, group in named.items() if (worst := max((_violation(c) for c in group), default=0.0)) > TOLERANCE}
    return Solved(account_id, strategy, _trades(m, account_id), _positions(m), _summary(m, float(problem.value)), violations, time.perf_counter() - start)


def _round_buys(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray) -> np.ndarray:
    """Down to a multiple of `increment`; nothing below `min_piece`. The epsilon makes 4999.9999 a 5000 lot."""
    rounded = np.floor(par / increment + 1e-6) * increment
    return np.where(rounded >= min_piece, rounded, 0.0)


def _round_sells(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray, lot_par: np.ndarray) -> np.ndarray:
    """Up to a multiple of `increment`, at least `min_piece`, at most the whole lot. Sub-dollar sells are solver noise."""
    rounded = np.maximum(np.ceil(par / increment - 1e-6) * increment, min_piece)
    return np.where(par > 0.5, np.minimum(rounded, lot_par), 0.0)


def _value(expression: cp.Expression) -> np.ndarray:
    if (value := expression.value) is None:
        msg = f"{expression} has no value: the problem is unsolved"
        raise ValueError(msg)
    return value


def _violation(constraint: cp.Constraint) -> float:
    return float(np.max(constraint.violation(), initial=0.0))


def _trades(m: Model, account_id: str) -> pd.DataFrame:
    buy, sell = _value(m.buy), _value(m.sell)
    buys = pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[buy > 0], "side": "buy", "par": buy[buy > 0]})
    sells = pd.DataFrame({"cusip": m.lots["cusip"].to_numpy()[sell > 0], "lot_id": m.lots["lot_id"].to_numpy()[sell > 0], "side": "sell", "par": sell[sell > 0]})
    trades = pd.concat([buys, sells], ignore_index=True).assign(account_id=account_id)
    return trades.astype({"account_id": "str", "cusip": "str", "lot_id": "str", "side": "str", "par": "int64"})[["account_id", "cusip", "lot_id", "side", "par"]]


def _positions(m: Model) -> pd.DataFrame:
    par = _value(m.par)
    held = par > 0.5
    return pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[held], "par": par[held].round().astype("int64"), "weight": _value(m.weights)[held]})


def _summary(m: Model, objective: float) -> dict[str, float]:
    return {
        "objective": objective,  # the LP optimum before rounding, as a fraction of value
        "value": m.value,
        "cash": float(_value(m.cash)),
        "turnover": float(_value(m.turnover)),
        "tax": float(_value(m.tax)),
        "cost": float(_value(m.cost)),
        "income": float(_value(m.income)),
        "tracking": float(np.abs(_value(m.weights) - m.target).sum()) + m.untracked,
    }
