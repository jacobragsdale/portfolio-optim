"""The model: what is bought per security and sold per tax lot, and the expressions that the objective and
constraints are written against, as numpy and sparse scipy arrays.

Selling by lot rather than by bond is what makes the optimizer tax-aware: it chooses which lots to sell, of those
`sellable`. Weights are fractions of pre-trade value, so constraints written in weights share one scale and one
feasibility tolerance.

An integer solve trades exactly what the market trades: whole increments, at least the minimum piece, leaving at least
the minimum remaining. An LP solve trades any amount, and `rounding` makes it tradable after the solve.

TODO(port): the old optimizer's formulation decides this module: how gains are taxed (`gain_rate`), what counts in value
and cash, and how trade sizes are modelled. Add an expression here when more than one constraint or term needs it.
"""

from dataclasses import dataclass
from typing import NamedTuple

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp

from optim.inputs import SolverInputs


@dataclass(frozen=True)
class OptimizationModel:
    inputs: SolverInputs  # what the rules produced: the portfolio, its lots, `as_of_date`; its `securities` is the whole market
    securities: pd.DataFrame  # in variable order: the bonds that can be bought or are held
    lots: pd.DataFrame  # in variable order
    lot_rows: np.ndarray  # the securities row of each lot
    buy: cp.Expression  # par bought, per security
    sell: cp.Expression  # par sold, per lot
    buying: cp.Expression | None  # 1 where a bond is bought, else 0; integer solves only
    selling: cp.Expression | None  # 1 where a lot is sold, in whole or in part, else 0; integer solves only
    gain: np.ndarray  # per lot, $ per $1 par sold: price less basis; a loss is negative
    gain_rate: np.ndarray  # per lot: the long-term rate if held over a year, else the short-term one
    value: float  # pre-trade value including cash: the fixed denominator of every weight
    par: cp.Expression  # post-trade par, per security
    weights: cp.Expression  # post-trade weight, per security
    target: np.ndarray  # target weight, per security
    untracked: float  # target weight outside the model (neither held nor buyable): a constant part of tracking error
    cash: cp.Expression  # post-trade cash, $
    turnover: cp.Expression  # value traded, $
    tax: cp.Expression  # tax on realized gains, $; negative when harvesting losses
    cost: cp.Expression  # trading costs, $
    income: cp.Expression  # expected return, $ per year
    bounds: list[cp.Constraint]  # what every solve needs: the trade sizes the market allows, and no overspending


def build_model(inputs: SolverInputs, *, integer: bool) -> OptimizationModel:
    s, lots, portfolio = inputs.securities, inputs.portfolio.lots, inputs.portfolio.details
    # Variables only for what can be bought or is held. A target bond outside that set is a constant in tracking
    # error, so the model shrinks from the whole benchmark to the tradable set without changing the answer.
    in_model = (s["buyable"] | s["cusip"].isin(lots["cusip"])).to_numpy()
    target = inputs.target.reindex(s["cusip"], fill_value=0.0).to_numpy()
    sec = s.loc[in_model].reset_index(drop=True)
    lot_rows = pd.Index(sec["cusip"]).get_indexer(pd.Index(lots["cusip"]))
    if (lot_rows < 0).any():
        msg = f"portfolio {portfolio.portfolio_id} holds bonds with no analytics: {lots.loc[lot_rows < 0, 'cusip'].head().tolist()}"
        raise LookupError(msg)

    n, k = len(sec), len(lots)
    holds = sp.csr_array((np.ones(k), (lot_rows, np.arange(k))), shape=(n, k))  # security x lot
    price = sec["price"].to_numpy() / 100  # $ per $1 par
    lot_par = lots["par"].to_numpy(dtype=float)
    value = float(portfolio.cash + price @ (holds @ lot_par))
    if value <= 0:
        msg = f"portfolio {portfolio.portfolio_id} has nothing to invest"
        raise ValueError(msg)

    offer = np.where(sec["buyable"], sec["offer_par"].fillna(0), 0).astype(float)
    sellable = lots["sellable"].to_numpy(dtype=bool)
    trades = _integer_trades(sec, lot_rows, lot_par, offer, sellable) if integer else _any_trades(offer, np.where(sellable, lot_par, 0.0))
    sold = holds @ trades.sell
    par = holds @ lot_par + trades.buy - sold
    cost = (price * sec["cost_bps"].to_numpy() / 1e4) @ (trades.buy + sold)
    cash = portfolio.cash + price @ (sold - trades.buy) - cost
    gain = price[lot_rows] - lots["cost_price"].to_numpy() / 100  # per $1 par
    long_term = (lots["acquired"] + pd.DateOffset(years=1) < pd.Timestamp(inputs.as_of_date)).to_numpy()  # held over a year
    gain_rate = np.where(long_term, portfolio.lt_gain_rate, portfolio.st_gain_rate)
    return OptimizationModel(
        inputs=inputs,
        securities=sec,
        lots=lots,
        lot_rows=lot_rows,
        gain=gain,
        gain_rate=gain_rate,
        buy=trades.buy,
        sell=trades.sell,
        buying=trades.buying,
        selling=trades.selling,
        value=value,
        par=par,
        weights=cp.multiply(price, par) / value,
        target=target[in_model],
        untracked=float(target[~in_model].sum()),
        cash=cash,
        turnover=price @ (trades.buy + sold),
        tax=(gain_rate * gain) @ trades.sell,
        cost=cost,
        income=(sec["expected_return"].to_numpy() * price) @ par,
        bounds=[*trades.sizes, cash / value >= 0],
    )


class _Trades(NamedTuple):
    buy: cp.Expression
    sell: cp.Expression
    buying: cp.Expression | None
    selling: cp.Expression | None
    sizes: list[cp.Constraint]  # the trade sizes the market allows


def _any_trades(offer: np.ndarray, sellable_par: np.ndarray) -> _Trades:
    """Any amount up to what is offered, or held in a sellable lot: an LP."""
    buy, sell = cp.Variable(len(offer), nonneg=True, name="buy"), cp.Variable(len(sellable_par), nonneg=True, name="sell")
    return _Trades(buy, sell, None, None, [buy <= offer, sell <= sellable_par])


def _integer_trades(sec: pd.DataFrame, lot_rows: np.ndarray, lot_par: np.ndarray, offer: np.ndarray, sellable: np.ndarray) -> _Trades:
    """Exactly what the market trades. A buy is nothing, or whole increments of at least the minimum piece. A sellable lot
    is sold whole, which is how an odd lot goes, or in part: whole increments of at least the minimum piece, leaving at
    least the minimum remaining. Each 0/1 flag is bounded by the trade's own size, so the formulation stays as tight as it
    can."""
    # assumes the minimum piece is a whole number of increments, as it is for nearly every bond; a minimum
    # piece plus whole increments is exact for the rest, and measured about 1.7x slower
    increment, min_piece = sec["increment"].to_numpy(dtype=float), sec["min_piece"].to_numpy(dtype=float)
    most_partial = np.maximum(lot_par - sec["min_remaining"].to_numpy(dtype=float)[lot_rows], 0)  # what a partial sale may take
    buy_steps, buying = cp.Variable(len(sec), integer=True, name="buy_steps"), cp.Variable(len(sec), boolean=True, name="buying")
    sell_steps = cp.Variable(len(lot_par), integer=True, name="sell_steps")
    whole, part = cp.Variable(len(lot_par), boolean=True, name="sell_whole"), cp.Variable(len(lot_par), boolean=True, name="sell_part")
    buy = cp.multiply(increment, buy_steps)
    partial = cp.multiply(increment[lot_rows], sell_steps)
    sizes = [
        buy_steps >= 0,
        buy >= cp.multiply(min_piece, buying),
        buy <= cp.multiply(offer, buying),
        sell_steps >= 0,
        partial >= cp.multiply(min_piece[lot_rows], part),
        partial <= cp.multiply(most_partial, part),
        whole + part <= sellable.astype(float),
    ]
    return _Trades(buy, cp.multiply(lot_par, whole) + partial, buying, whole + part, sizes)


def value_of(expression: cp.Expression) -> np.ndarray:
    """An expression's value at the variables' current values."""
    if (value := expression.value) is None:
        msg = f"{expression} has no value: the problem is unsolved"
        raise ValueError(msg)
    return value
