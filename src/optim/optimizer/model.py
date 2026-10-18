"""The model: what is bought and sold per line, one sleeve's holding of one security, and the expressions that the
objective and constraints are written against, as numpy and sparse scipy arrays.

Values are quantities times their security's `unit_value`, in the portfolio's currency. The sleeves share the
portfolio's cash: after trading, it is the opening cash plus cash flows, plus sales, less purchases and estimated trading
costs.

Weights are values over a scope's base value (`Scope`): the whole portfolio's, or one sleeve's. Base values are
constants, so a constraint or term in weights stays linear in the trades, and every one shares one tolerance.

An integer solve trades exactly what the market trades: a buy is whole increments of at least the minimum piece; a sale
is the whole holding, exactly, or whole increments of at least the minimum piece leaving at least the minimum remaining.
An LP solve trades any amount, and `rounding` makes it tradable after the solve.

TODO(port): the old optimizer's formulation decides this module: what counts in value and cash, and how trade sizes are
modelled. Add an expression here when more than one constraint or term needs it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp

from optim.data.frames import to_models
from optim.datasets.portfolios import Sleeve
from optim.inputs import SolverInputs
from optim.schema import Id, StrictModel

PORTFOLIO = "portfolio"  # the scope of the whole portfolio, beside one per sleeve


@dataclass(frozen=True)
class Scope:
    """What a constraint or term covers: the whole portfolio, or one sleeve. Its weights are values over `base`.

    Benchmark weights are scaled to the scope: a sleeve's benchmark, whose weights sum to 1, is its target in its own
    weights. The portfolio's is the sum of its sleeves', each times the sleeve's share of the portfolio's base value;
    what the shares leave, such as cash, has no benchmark."""

    name: str  # `PORTFOLIO` or a sleeve's id
    members: np.ndarray  # per line: in the scope
    base: float  # $: the portfolio's base value, or the sleeve's
    target: np.ndarray  # per line: the benchmark weight it is measured against, in the scope's weights; 0 outside it
    benchmark: pd.Series  # weight by security_id, in the scope's weights: every constituent, a line or not


class Scoped(StrictModel):
    """Base for a constraint or term that covers the whole portfolio or one sleeve: `m.scope(self.sleeve)`."""

    sleeve: Id | None = None
    """The sleeve it covers, by id; null: the whole portfolio. A portfolio without that sleeve fails."""


@dataclass(frozen=True)
class OptimizationModel:
    inputs: SolverInputs  # what the rules produced: the portfolio, its cash, `as_of_date`, the market
    lines: pd.DataFrame  # in variable order: the lines that can be bought or are held
    buy: cp.Expression  # quantity bought, per line
    sell: cp.Expression  # quantity sold, per line
    buying: cp.Expression | None  # 1 where a line is bought, else 0; integer solves only
    selling: cp.Expression | None  # 1 where a line is sold, in whole or in part, else 0; integer solves only
    held: np.ndarray  # quantity held before trading, per line
    unit_value: np.ndarray  # $ per unit of quantity, per line
    quantity: cp.Expression  # quantity held after trading, per line
    value: float  # pre-trade value, cash included, before cash flows
    base: float  # the portfolio's base value: its target value, or its value after cash flows
    cash: cp.Expression  # after trading and cash flows, $
    turnover: cp.Expression  # value bought plus value sold, $
    cost: cp.Expression  # estimated trading costs, $
    income: cp.Expression  # expected return, $ per year
    bounds: list[cp.Constraint]  # what every solve needs: the trade sizes the market and the controls allow, and no overspending
    scopes: Mapping[str, Scope]  # `PORTFOLIO`, and each sleeve by its id
    lp: LpTrades | None  # an LP solve's variables, which `rounding` sets

    def scope(self, sleeve: str | None) -> Scope:
        """The scope a constraint or term names: a sleeve, or with None the whole portfolio."""
        name = PORTFOLIO if sleeve is None else sleeve
        if (scope := self.scopes.get(name)) is None:
            msg = f"portfolio {self.inputs.portfolio.details.portfolio_id} has no sleeve {sleeve!r}; it has {sorted(self.scopes.keys() - {PORTFOLIO})}"
            raise LookupError(msg)
        if scope.base <= 0:
            msg = f"sleeve {sleeve} of portfolio {self.inputs.portfolio.details.portfolio_id} has no value to measure weights against: give it a target_weight"
            raise ValueError(msg)
        return scope

    def weights(self, scope: Scope) -> cp.Expression:
        """Post-trade weight per line in `scope`, 0 outside it."""
        return cp.multiply(scope.members * self.unit_value / scope.base, self.quantity)

    def benchmark(self, scope: Scope) -> pd.Series:
        """The scope's benchmark weights by security. A scope without one fails, rather than being measured against
        nothing, as the whole portfolio is when no sleeve with a benchmark has a target weight or a value."""
        if not scope.benchmark.sum() > 0:
            msg = f"scope {scope.name} of portfolio {self.inputs.portfolio.details.portfolio_id} has no benchmark: give its sleeves a benchmark_id, and a target_weight or holdings"
            raise ValueError(msg)
        return scope.benchmark

    def target(self, scope: Scope) -> np.ndarray:
        """Per line, the benchmark weight it is measured against, in the scope's weights; see `benchmark`."""
        self.benchmark(scope)
        return scope.target

    def exposure(self, scope: Scope, column: str) -> cp.Expression:
        """The scope's exposure to a numeric security column, such as `duration`: each line's weight times its value of
        the column, summed, cash counting as 0. A line in the scope without a value fails it."""
        values = self.lines[column].to_numpy(dtype=float, na_value=np.nan)
        if (missing := scope.members & np.isnan(values)).any():
            msg = f"{column} is missing for securities in scope {scope.name}, e.g. {self.lines.loc[missing, 'security_id'].head(3).tolist()}: an exposure cannot treat them as 0"
            raise ValueError(msg)
        exposure: cp.Expression = (scope.members * self.unit_value * np.nan_to_num(values) / scope.base) @ self.quantity
        return exposure

    def benchmark_exposure(self, scope: Scope, column: str) -> float:
        """The same sum over every constituent of the scope's benchmark, held or not."""
        benchmark = self.benchmark(scope)
        values = self.inputs.securities.set_index("security_id")[column].reindex(benchmark.index)
        if values.isna().any():
            msg = f"{column} is missing for benchmark constituents in scope {scope.name}, e.g. {values[values.isna()].index[:3].tolist()}"
            raise ValueError(msg)
        return float(benchmark.to_numpy() @ values.to_numpy(dtype=float))


def build_model(inputs: SolverInputs, *, integer: bool) -> OptimizationModel:
    details = inputs.portfolio.details
    # Variables only for what can be bought or is held. A constituent outside that set is a constant in tracking
    # error, so the model shrinks from the whole benchmark to the tradable set without changing the answer.
    in_model = inputs.lines["buyable"].to_numpy() | (inputs.lines["held"] > 0).to_numpy(dtype=bool)
    lines = inputs.lines.loc[in_model].reset_index(drop=True)
    held, unit_value = lines["held"].to_numpy(dtype=float), lines["unit_value"].to_numpy()
    value = inputs.cash + float(unit_value @ held)
    base = float(details.target_value) if details.target_value is not None else value + inputs.cash_flow
    if base <= 0:
        msg = f"portfolio {details.portfolio_id} has nothing to invest: its base value is {base:,.2f}"
        raise ValueError(msg)

    # the most any line could buy: everything the portfolio could spend, or what is available
    spendable = (value + max(inputs.cash_flow, 0.0)) / unit_value
    most = np.where(lines["buyable"], np.fmin(lines["available"].to_numpy(dtype=float), spendable), 0.0)
    must_sell = lines["must_sell"].to_numpy(dtype=bool)
    to_buy, to_sell = np.flatnonzero(lines["buyable"]), np.flatnonzero(lines["sellable"])
    if integer:
        trades = _integer_trades(lines, held, most, must_sell=must_sell, to_buy=to_buy, to_sell=to_sell)
    else:
        trades = _any_trades(lines, held, most, must_sell=must_sell, to_buy=to_buy, to_sell=to_sell, per_weight=base / unit_value)
    quantity = held + trades.buy - trades.sell
    cost = (unit_value * lines["cost_bps"].to_numpy() / 1e4) @ (trades.buy + trades.sell)
    cash = inputs.cash + inputs.cash_flow + unit_value @ (trades.sell - trades.buy) - cost
    # inventory bounds what all of a security's lines buy together: the least that any list offering it gives
    offered = lines["security_id"].where(lines["buyable"] & lines["available"].notna())
    securities, members = indicator(offered)
    inventory = [members @ trades.buy <= lines.groupby(offered)["available"].min().reindex(securities).to_numpy()] if len(securities) else []
    return OptimizationModel(
        inputs=inputs,
        lines=lines,
        buy=trades.buy,
        sell=trades.sell,
        buying=trades.buying,
        selling=trades.selling,
        held=held,
        unit_value=unit_value,
        quantity=quantity,
        value=value,
        base=base,
        cash=cash,
        turnover=unit_value @ (trades.buy + trades.sell),
        cost=cost,
        income=(lines["expected_return"].to_numpy() * unit_value) @ quantity,
        bounds=[*trades.sizes, *inventory, cash / base >= 0],
        scopes=_scopes(inputs, lines, unit_value * held, base),
        lp=trades.lp,
    )


class LpTrades(NamedTuple):
    """An LP solve's variables: weights bought and sold, of the lines that can be, and the quantity one weight is."""

    buy: cp.Variable
    sell: cp.Variable
    to_buy: np.ndarray  # the line each `buy` entry is
    to_sell: np.ndarray
    per_weight: np.ndarray  # per line: quantity per unit of weight, base value over unit value


class _Trades(NamedTuple):
    buy: cp.Expression  # per line
    sell: cp.Expression  # per line
    buying: cp.Expression | None
    selling: cp.Expression | None
    sizes: list[cp.Constraint]  # the trade sizes the market and the controls allow
    lp: LpTrades | None


def _any_trades(lines: pd.DataFrame, held: np.ndarray, most: np.ndarray, *, must_sell: np.ndarray, to_buy: np.ndarray, to_sell: np.ndarray, per_weight: np.ndarray) -> _Trades:
    """Any amount up to the most a line could buy, or what it holds: an LP. A mandatory sale sells it all. The variables
    are weights, not quantities, so the solver sees numbers near 1: a QP solver given quantities of a million stops
    early, or takes zero for optimal."""
    b, s = to_buy, to_sell
    buy, sell = cp.Variable(len(b), nonneg=True, name="buy"), cp.Variable(len(s), nonneg=True, name="sell")
    sizes = [buy <= most[b] / per_weight[b], sell <= held[s] / per_weight[s], sell >= np.where(must_sell[s], held[s] / per_weight[s], 0.0)]
    n = len(lines)
    trades = _onto(b, n) @ cp.multiply(per_weight[b], buy), _onto(s, n) @ cp.multiply(per_weight[s], sell)
    return _Trades(*trades, None, None, sizes, LpTrades(buy, sell, b, s, per_weight))


def _integer_trades(lines: pd.DataFrame, held: np.ndarray, most: np.ndarray, *, must_sell: np.ndarray, to_buy: np.ndarray, to_sell: np.ndarray) -> _Trades:
    """Exactly what the market trades. A buy is nothing, or whole increments of at least the minimum piece. A sale is the
    whole holding, which is how an odd amount goes and how a mandatory sale must go, or a part: whole increments of at
    least the minimum piece, leaving at least the minimum remaining; a security without denominations is only sold
    whole. Each 0/1 flag is bounded by the trade's own size, so the formulation stays as tight as it can."""
    # assumes the minimum piece is a whole number of increments, as it is for nearly every bond; a minimum
    # piece plus whole increments is exact for the rest, and measured about 1.7x slower
    increment, min_piece, min_remaining = (lines[column].to_numpy(dtype=float, na_value=0.0) for column in ("increment", "min_piece", "min_remaining"))
    b, s = to_buy, to_sell
    buy_steps, buying = cp.Variable(len(b), integer=True, name="buy_steps"), cp.Variable(len(b), boolean=True, name="buying")
    sell_steps = cp.Variable(len(s), integer=True, name="sell_steps")
    whole, part = cp.Variable(len(s), boolean=True, name="sell_whole"), cp.Variable(len(s), boolean=True, name="sell_part")
    buy = cp.multiply(increment[b], buy_steps)
    partial = cp.multiply(increment[s], sell_steps)
    most_partial = np.where(increment[s] > 0, np.maximum(held[s] - min_remaining[s], 0.0), 0.0)
    sizes = [
        buy_steps >= 0,
        buy >= cp.multiply(min_piece[b], buying),
        buy <= cp.multiply(most[b], buying),
        sell_steps >= 0,
        partial >= cp.multiply(min_piece[s], part),
        partial <= cp.multiply(most_partial, part),
        whole + part <= 1,
        whole >= must_sell[s].astype(float),
    ]
    n = len(lines)
    sell = cp.multiply(held[s], whole) + partial
    return _Trades(_onto(b, n) @ buy, _onto(s, n) @ sell, _onto(b, n) @ buying, _onto(s, n) @ (whole + part), sizes, None)


def _onto(rows: np.ndarray, n: int) -> sp.csr_array:
    """Places a vector with one entry per row of `rows` onto all `n` lines, 0 elsewhere."""
    return sp.csr_array((np.ones(len(rows)), (rows, np.arange(len(rows)))), shape=(n, len(rows)))


def _scopes(inputs: SolverInputs, lines: pd.DataFrame, pre_trade: np.ndarray, base: float) -> dict[str, Scope]:
    """The portfolio's scope and each sleeve's. A sleeve's base value is its target weight of the portfolio's, or else
    its value before trading."""
    security_ids = pd.Index(lines["security_id"])
    scopes: dict[str, Scope] = {}
    targets, benchmarks = np.zeros(len(lines)), []
    for sleeve in to_models(Sleeve, inputs.portfolio.sleeves):
        mine = (lines["sleeve_id"] == sleeve.sleeve_id).to_numpy()
        sleeve_base = base * sleeve.target_weight if sleeve.target_weight is not None else float(pre_trade[mine].sum())
        benchmark = inputs.benchmarks[sleeve.benchmark_id] if sleeve.benchmark_id is not None else pd.Series(dtype=float)
        target = np.where(mine, benchmark.reindex(security_ids, fill_value=0.0).to_numpy(), 0.0)
        scopes[sleeve.sleeve_id] = Scope(sleeve.sleeve_id, mine, sleeve_base, target, benchmark)
        share = sleeve_base / base
        targets += share * target
        if share > 0:
            benchmarks.append(share * benchmark)
    blended = pd.concat(benchmarks).groupby(level=0).sum() if benchmarks else pd.Series(dtype=float)
    return {PORTFOLIO: Scope(PORTFOLIO, np.ones(len(lines), dtype=bool), base, targets, blended)} | scopes


def indicator(labels: pd.Series) -> tuple[pd.Index, sp.csr_array]:
    """The distinct labels, and a sparse groups x rows 0/1 membership matrix. Missing labels belong to no group."""
    codes, groups = pd.factorize(labels)
    rows = np.flatnonzero(codes >= 0)
    return pd.Index(groups), sp.csr_array((np.ones(len(rows)), (codes[rows], rows)), shape=(len(groups), len(labels)))


def value_of(expression: cp.Expression) -> np.ndarray:
    """An expression's value at the variables' current values."""
    if (value := expression.value) is None:
        msg = f"{expression} has no value: the problem is unsolved"
        raise ValueError(msg)
    return value
