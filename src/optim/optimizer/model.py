"""The model: two cvxpy variables, par bought per security and par sold per tax lot, and the expressions that the
objective and constraints are written against, as numpy and sparse scipy arrays.

Selling by lot rather than by bond is what makes the optimizer tax-aware: it chooses which lots to sell. Weights are
fractions of pre-trade value, so constraints written in weights share one scale and one feasibility tolerance.
"""

from dataclasses import dataclass

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp

from optim.inputs import Inputs


@dataclass(frozen=True)
class Model:
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


def build_model(inputs: Inputs) -> Model:
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


def value_of(expression: cp.Expression) -> np.ndarray:
    """An expression's value at the variables' current values."""
    if (value := expression.value) is None:
        msg = f"{expression} has no value: the problem is unsolved"
        raise ValueError(msg)
    return value
