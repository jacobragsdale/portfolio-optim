"""What a solve returns: `Solved`, with trades, positions and a summary at tradable lots, or `Unsolved`."""

from dataclasses import dataclass
from typing import Self

import numpy as np
import pandas as pd

from optim.optimizer.model import Model, value_of


@dataclass(frozen=True)
class Solved:
    account_id: str
    strategy: str
    trades: pd.DataFrame  # account_id, cusip, lot_id (sells only), side, par
    positions: pd.DataFrame  # cusip, par, weight after trading
    summary: dict[str, float]  # objective; value, cash, turnover, tax, cost, income in $; tracking as L1 weight distance
    violations: dict[str, float]  # constraint -> worst excess in weights, after rounding to tradable lots
    seconds: float
    solver_inputs: pd.DataFrame  # see `solver_inputs`

    @property
    def ok(self) -> bool:
        """Safe to trade: rounding broke no constraint."""
        return not self.violations

    @property
    def status(self) -> str:
        return "ok" if self.ok else f"rounding broke {sorted(self.violations)}"

    @classmethod
    def from_model(cls, m: Model, *, account_id: str, strategy: str, objective: float, violations: dict[str, float], seconds: float) -> Self:
        """Read the result off a model whose variables hold the rounded trades."""
        return cls(account_id, strategy, _trades(m, account_id), _positions(m), _summary(m, objective), violations, seconds, solver_inputs(m))


@dataclass(frozen=True)
class Unsolved:
    account_id: str
    strategy: str
    status: str  # the solver's status ("infeasible", ...) or the error that stopped the solve
    solver_inputs: pd.DataFrame | None = None  # when the solver ran


type Result = Solved | Unsolved


def solver_inputs(m: Model) -> pd.DataFrame:
    """Exactly what the LP saw for each security in it, after the rules: the securities columns, with `buyable` and
    `expected_return` as the rules left them, and the target weight. With the snapshot's lots, accounts and request, it
    is the whole problem."""
    return m.securities.assign(target=m.target)


def _trades(m: Model, account_id: str) -> pd.DataFrame:
    buy, sell = value_of(m.buy), value_of(m.sell)
    buys = pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[buy > 0], "side": "buy", "par": buy[buy > 0]})
    sells = pd.DataFrame({"cusip": m.lots["cusip"].to_numpy()[sell > 0], "lot_id": m.lots["lot_id"].to_numpy()[sell > 0], "side": "sell", "par": sell[sell > 0]})
    trades = pd.concat([buys, sells], ignore_index=True).assign(account_id=account_id)
    return trades.astype({"account_id": "str", "cusip": "str", "lot_id": "str", "side": "str", "par": "int64"})[["account_id", "cusip", "lot_id", "side", "par"]]


def _positions(m: Model) -> pd.DataFrame:
    par = value_of(m.par)
    held = par > 0.5
    return pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[held], "par": par[held].round().astype("int64"), "weight": value_of(m.weights)[held]})


def _summary(m: Model, objective: float) -> dict[str, float]:
    return {
        "objective": objective,  # the LP optimum before rounding, as a fraction of value
        "value": m.value,
        "cash": float(value_of(m.cash)),
        "turnover": float(value_of(m.turnover)),
        "tax": float(value_of(m.tax)),
        "cost": float(value_of(m.cost)),
        "income": float(value_of(m.income)),
        "tracking": float(np.abs(value_of(m.weights) - m.target).sum()) + m.untracked,
    }
