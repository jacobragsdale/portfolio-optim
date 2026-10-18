"""Whether a solve's result may trade. A solver's optimum means it solved the model it was given, within its tolerances;
it is not permission to trade. So once the trades are exact, `review` rebuilds the result from them, apart from the
model: each trade's exact quantity (`Decimal`), the holdings and cash after trading, and every dollar in the summary.
Then it runs these checks, and a result that fails any is saved as rejected, and makes the run exit nonzero:

- `sizes`: every trade is a size the market trades. A buy is whole increments of at least the minimum piece, and a
  security's buys across its lines are within what is available; a sale is the whole holding exactly, or whole
  increments of at least the minimum piece that leave at least the minimum remaining.
- `controls`: nothing bought that may not be, nothing sold that may not be, and every mandatory sale sold whole.
- `cash`: cash after trading and cash flows is not negative.
- `model`: the model's cash and holdings after trading agree with the rebuilt ones, so the formulation counts as the
  books do.
- `solver`: the solver proved its answer optimal or stopped at a limit, and an integer answer is within the strategy's
  `max_gap` of the best possible, as the solver reports its gap.
- each of the strategy's constraints, by its place and kind, such as `2:max_turnover`, holds at the exact trades.

Each check's excess is how far it is broken: lines for `sizes` and `controls`, the gap past `max_gap` for `solver` (1 for
an answer with no gap to judge it by), and weights for the rest, over `TOLERANCE`."""

import time
from collections.abc import Mapping
from decimal import Decimal

import cvxpy as cp
import numpy as np
import pandas as pd

from optim.optimizer.backends import Termination
from optim.optimizer.model import PORTFOLIO, OptimizationModel, value_of
from optim.optimizer.results import Solved

TOLERANCE = 1e-6  # in weights: $1 per $1mm of base value


def review(m: OptimizationModel, constraints: Mapping[str, list[cp.Constraint]], termination: Termination, *, strategy: str, objective: float, max_gap: float, start: float) -> Solved:
    """The result at the model's current values, which `rounding` made exact, with every check it fails."""
    lines, held, base = m.lines, m.held, m.base
    buy, sell = value_of(m.buy), value_of(m.sell)
    whole = (sell > 0) & (np.abs(sell - held) <= 1e-6)  # the whole holding, as booked, however many units it is
    bought, sold = np.round(buy), np.where(whole, held, np.round(sell))
    after = held + bought - sold
    traded = m.unit_value * (bought + sold)
    cost = float(traded @ lines["cost_bps"].to_numpy() / 1e4)
    cash = m.inputs.cash + m.inputs.cash_flow + float(m.unit_value @ (sold - bought)) - cost
    drift = max(abs(float(value_of(m.cash)) - cash), float(np.max(np.abs(value_of(m.quantity) - after) * m.unit_value, initial=0.0))) / base

    failed: dict[str, float] = {}
    if bad := int(_bad_sizes(lines, held, buy=buy, sell=sell, bought=bought, sold=sold, whole=whole).sum()):
        failed["sizes"] = bad
    if bad := int(_bad_controls(lines, bought, sold, whole).sum()):
        failed["controls"] = bad
    if cash < -TOLERANCE * base:
        failed["cash"] = -cash / base
    if drift > TOLERANCE:
        failed["model"] = drift
    if (excess := _past_policy(termination, max_gap)) > 0:
        failed["solver"] = excess
    failed |= violations(constraints)

    portfolio = m.scopes[PORTFOLIO]
    weights = m.unit_value * after / base
    summary: dict[str, object] = {
        "stop": termination.stop,
        "objective": objective,
        "gap": termination.gap,
        "nodes": termination.nodes,
        "value": m.value,
        "base": base,
        "cash_flow": m.inputs.cash_flow,
        "cash": cash,
        "turnover": float(traded.sum()),
        "cost": cost,
        "income": float((lines["expected_return"].to_numpy() * m.unit_value) @ after),
        "tracking": float(np.abs(weights - portfolio.target).sum() + portfolio.benchmark.sum() - portfolio.target.sum()) if portfolio.benchmark.sum() > 0 else None,
    }
    trades, positions = _trades(m, bought, sold, whole), _positions(m, bought, sold, whole, weights)
    portfolio_id = m.inputs.portfolio.details.portfolio_id
    return Solved(portfolio_id, strategy, trades.assign(portfolio_id=portfolio_id), positions, summary, failed, time.perf_counter() - start)


def violations(constraints: Mapping[str, list[cp.Constraint]]) -> dict[str, float]:
    """The worst excess of each named group of constraints at the current values, where it is over `TOLERANCE`."""
    worst = {name: max((float(np.max(c.violation(), initial=0.0)) for c in group), default=0.0) for name, group in constraints.items()}
    return {name: excess for name, excess in worst.items() if excess > TOLERANCE}


def _past_policy(termination: Termination, max_gap: float) -> float:
    """A solver calls an integer answer optimal once it is within its own gap option, which a strategy can loosen, so
    `max_gap` judges every answer with a gap, optimal or stopped at a limit."""
    if termination.stop not in ("optimal", "limit"):
        return 1.0  # the solver doubts its answer
    if termination.gap is not None:
        return max(termination.gap - max_gap, 0.0)
    return 0.0 if termination.stop == "optimal" else 1.0  # an LP stopped early has no gap to judge it by


def _bad_sizes(lines: pd.DataFrame, held: np.ndarray, *, buy: np.ndarray, sell: np.ndarray, bought: np.ndarray, sold: np.ndarray, whole: np.ndarray) -> np.ndarray:
    increment, min_piece, min_remaining = (lines[column].to_numpy(dtype=float, na_value=0.0) for column in ("increment", "min_piece", "min_remaining"))
    # a security's inventory bounds its lines together, as in the model; NaN, no limit, compares False
    security = lines["security_id"]
    available = lines["available"].where(lines["buyable"]).groupby(security).transform("min").to_numpy(dtype=float)
    bought_of_security = pd.Series(bought).groupby(security.to_numpy()).transform("sum").to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):  # no denominations: increment 0, and no partial trade is whole steps

        def steps(quantity: np.ndarray) -> np.ndarray:
            return np.abs(quantity / increment - np.round(quantity / increment)) <= 1e-9

        units = np.abs(buy - bought) <= 1e-6  # the solver's answer was whole units, before `np.round`
        bad_buys = (bought > 0) & (~units | ~steps(bought) | (bought < min_piece) | (bought_of_security > available + 1e-6))
        partial = (sold > 0) & ~whole
        units = np.abs(sell - sold) <= 1e-6
        bad_sales = partial & (~units | ~steps(sold) | (sold < min_piece) | (held - sold < min_remaining - 1e-6))
    return bad_buys | bad_sales


def _bad_controls(lines: pd.DataFrame, bought: np.ndarray, sold: np.ndarray, whole: np.ndarray) -> np.ndarray:
    must_sell = lines["must_sell"].to_numpy(dtype=bool)
    return ((bought > 0) & ~lines["buyable"].to_numpy(dtype=bool)) | ((sold > 0) & ~lines["sellable"].to_numpy(dtype=bool)) | (must_sell & ~whole)


def _exact(m: OptimizationModel, rows: np.ndarray, quantity: np.ndarray, whole: np.ndarray) -> np.ndarray:
    """Whole units as `Decimal`s, or the holding as booked where a sale is whole."""
    booked = m.lines["held"]
    return np.array([booked.iloc[i] if whole[i] else Decimal(int(quantity[i])) for i in rows], dtype=object)


def _trades(m: OptimizationModel, bought: np.ndarray, sold: np.ndarray, whole: np.ndarray) -> pd.DataFrame:
    lines, columns = m.lines, ["sleeve_id", "security_id", "cusip"]
    buys, sales = np.flatnonzero(bought > 0), np.flatnonzero(sold > 0)
    never = np.zeros(len(lines), dtype=bool)  # a buy is never a whole holding
    return pd.concat(
        [
            lines.loc[buys, columns].assign(side="buy", quantity=_exact(m, buys, bought, never), value=m.unit_value[buys] * bought[buys], liquidates=False, mandatory=False),
            lines.loc[sales, columns].assign(
                side="sell", quantity=_exact(m, sales, sold, whole), value=m.unit_value[sales] * sold[sales], liquidates=whole[sales], mandatory=lines["must_sell"].to_numpy(dtype=bool)[sales]
            ),
        ],
        ignore_index=True,
    )


def _positions(m: OptimizationModel, bought: np.ndarray, sold: np.ndarray, whole: np.ndarray, weights: np.ndarray) -> pd.DataFrame:
    after = m.held + bought - sold
    rows = np.flatnonzero(after > 1e-9)
    booked = m.lines["held"]
    quantity = np.array([booked.iloc[i] + Decimal(int(bought[i])) - sale for i, sale in zip(rows, _exact(m, rows, sold, whole), strict=True)], dtype=object)
    return m.lines.loc[rows, ["sleeve_id", "security_id"]].assign(quantity=quantity, value=m.unit_value[rows] * after[rows], weight=weights[rows]).reset_index(drop=True)
