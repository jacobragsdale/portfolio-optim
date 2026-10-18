"""From the solver's answer to exact trades, and back through the constraints to see whether any broke. An integer solve
is already tradable and only needs snapping; an LP solve is rounded to tradable lots."""

from collections.abc import Mapping

import cvxpy as cp
import numpy as np

from optim.optimizer.model import OptimizationModel, value_of

TOLERANCE = 1e-6  # in weights: $1 per $1mm of portfolio value


def snap_integers(problem: cp.Problem) -> None:
    """Integer and 0/1 variables to exact integers. Solvers return values such as 2.9999999, within their tolerance;
    snapped, the trades are exact, and `violations` checks exactly what would be traded."""
    for variable in problem.variables():
        if variable.size and (variable.attributes["integer"] or variable.attributes["boolean"]):  # cvxpy cannot set an empty one
            variable.value = np.round(value_of(variable))


def round_to_lots(m: OptimizationModel) -> None:
    """An LP solve's trades to tradable amounts, rounding toward cash: buys down, sells up. That keeps limits and the
    cash floor intact; turnover, tax and duration can still move past their bounds, which `violations` reports."""
    # a heuristic; when the constraints it breaks matter, solve with "integer": true
    buy, sell = m.buy, m.sell
    if not (isinstance(buy, cp.Variable) and isinstance(sell, cp.Variable)):
        msg = "round_to_lots rounds an LP solve; an integer solve is snapped with snap_integers"
        raise TypeError(msg)
    s, rows = m.securities, m.lot_rows
    increment, min_piece, min_remaining = s["increment"].to_numpy(), s["min_piece"].to_numpy(), s["min_remaining"].to_numpy()
    buy.value = _round_buys(value_of(buy), increment, min_piece)
    sell.value = _round_sells(value_of(sell), increment[rows], min_piece[rows], min_remaining[rows], m.lots["par"].to_numpy(dtype=float))


def violations(constraints: Mapping[str, list[cp.Constraint]]) -> dict[str, float]:
    """The worst excess of each named group of constraints at the current values, where it is over `TOLERANCE`."""
    worst = {name: max((float(np.max(c.violation(), initial=0.0)) for c in group), default=0.0) for name, group in constraints.items()}
    return {name: excess for name, excess in worst.items() if excess > TOLERANCE}


def _round_buys(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray) -> np.ndarray:
    """Down to a multiple of `increment`; nothing below `min_piece`. The epsilon makes 4999.9999 a 5000 lot."""
    rounded = np.floor(par / increment + 1e-6) * increment
    return np.where(rounded >= min_piece, rounded, 0.0)


def _round_sells(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray, min_remaining: np.ndarray, lot_par: np.ndarray) -> np.ndarray:
    """Up to a multiple of `increment`, at least `min_piece`; the whole lot when less than `min_remaining` would be left.
    Sub-dollar sells are solver noise."""
    rounded = np.maximum(np.ceil(par / increment - 1e-6) * increment, min_piece)
    return np.where(par > 0.5, np.where(lot_par - rounded < min_remaining, lot_par, rounded), 0.0)
