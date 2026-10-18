"""From the solver's answer to exact trades. An integer solve is already tradable and only needs snapping; an LP solve is
rounded to tradable lots. `acceptance` then checks what either produced."""

import cvxpy as cp
import numpy as np

from optim.optimizer.model import OptimizationModel, value_of


def snap_integers(problem: cp.Problem) -> None:
    """Integer and 0/1 variables to exact integers. Solvers return values such as 2.9999999, within their tolerance;
    snapped, the trades are exact, and acceptance checks exactly what would be traded."""
    for variable in problem.variables():
        if variable.size and (variable.attributes["integer"] or variable.attributes["boolean"]):  # cvxpy cannot set an empty one
            variable.value = np.round(value_of(variable))


def round_to_lots(m: OptimizationModel) -> None:
    """An LP solve's trades to tradable amounts, rounding toward cash: buys down, sales up. That keeps limits and the cash
    floor intact; turnover and other bounds can still move past their limits, which acceptance reports."""
    # a heuristic; when the constraints it breaks matter, solve with "integer": true
    if m.lp is None:
        msg = "round_to_lots rounds an LP solve; an integer solve is snapped with snap_integers"
        raise TypeError(msg)
    lp = m.lp
    increment, min_piece, min_remaining = (m.lines[column].to_numpy(dtype=float, na_value=0.0) for column in ("increment", "min_piece", "min_remaining"))
    b, s, per_weight = lp.to_buy, lp.to_sell, lp.per_weight
    bought = _round_buys(value_of(lp.buy) * per_weight[b], increment[b], min_piece[b])
    sold = _round_sells(value_of(lp.sell) * per_weight[s], increment[s], min_piece=min_piece[s], min_remaining=min_remaining[s], held=m.held[s], must_sell=m.lines["must_sell"].to_numpy(dtype=bool)[s])
    lp.buy.value, lp.sell.value = bought / per_weight[b], sold / per_weight[s]


def _round_buys(quantity: np.ndarray, increment: np.ndarray, min_piece: np.ndarray) -> np.ndarray:
    """Down to a multiple of `increment`; nothing below `min_piece`. The epsilon makes 4999.9999 a 5000 lot."""
    rounded = np.floor(quantity / increment + 1e-6) * increment
    return np.where(rounded >= min_piece, rounded, 0.0)


def _round_sells(quantity: np.ndarray, increment: np.ndarray, *, min_piece: np.ndarray, min_remaining: np.ndarray, held: np.ndarray, must_sell: np.ndarray) -> np.ndarray:
    """Up to a multiple of `increment`, at least `min_piece`; the whole holding when less than `min_remaining` would be
    left, when the security has no denominations, or when the sale is mandatory. Sales under one unit are solver noise."""
    with np.errstate(divide="ignore", invalid="ignore"):  # no denominations: increment 0, and the sale is whole
        rounded = np.maximum(np.ceil(quantity / increment - 1e-6) * increment, min_piece)
    whole = must_sell | (increment == 0) | (held - rounded < min_remaining)
    return np.where(must_sell | (quantity > 0.5), np.where(whole, held, rounded), 0.0)
