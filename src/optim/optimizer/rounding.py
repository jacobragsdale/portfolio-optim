"""From the LP's fractional par to tradable lots, and back through the constraints to see what rounding broke."""

from collections.abc import Mapping

import cvxpy as cp
import numpy as np

from optim.optimizer.model import Model, value_of

TOLERANCE = 1e-6  # in weights: $1 per $1mm of portfolio value


def round_to_lots(m: Model) -> None:
    """Replace the solution with tradable amounts, rounding toward cash: buys down, sells up. That keeps limits and the
    cash floor intact; turnover, tax and duration can still move past their bounds, which `violations` reports."""
    # ponytail: rounding heuristic; add a repair MIP over the traded bonds if violations turn out to be common
    increment, min_piece = m.securities["increment"].to_numpy(), m.securities["min_piece"].to_numpy()
    m.buy.value = _round_buys(value_of(m.buy), increment, min_piece)
    m.sell.value = _round_sells(value_of(m.sell), increment[m.lot_rows], min_piece[m.lot_rows], m.lots["par"].to_numpy(dtype=float))


def violations(constraints: Mapping[str, list[cp.Constraint]]) -> dict[str, float]:
    """The worst excess of each named group of constraints at the current values, where it is over `TOLERANCE`."""
    worst = {name: max((float(np.max(c.violation(), initial=0.0)) for c in group), default=0.0) for name, group in constraints.items()}
    return {name: excess for name, excess in worst.items() if excess > TOLERANCE}


def _round_buys(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray) -> np.ndarray:
    """Down to a multiple of `increment`; nothing below `min_piece`. The epsilon makes 4999.9999 a 5000 lot."""
    rounded = np.floor(par / increment + 1e-6) * increment
    return np.where(rounded >= min_piece, rounded, 0.0)


def _round_sells(par: np.ndarray, increment: np.ndarray, min_piece: np.ndarray, lot_par: np.ndarray) -> np.ndarray:
    """Up to a multiple of `increment`, at least `min_piece`, at most the whole lot. Sub-dollar sells are solver noise."""
    rounded = np.maximum(np.ceil(par / increment - 1e-6) * increment, min_piece)
    return np.where(par > 0.5, np.minimum(rounded, lot_par), 0.0)
