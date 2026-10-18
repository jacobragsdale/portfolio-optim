"""What a solve returns: `Solved`, with trades, positions and a summary at tradable lots, or `Unsolved`."""

from dataclasses import dataclass
from typing import Self

import numpy as np
import pandas as pd

from optim.optimizer.model import Model, value_of


@dataclass(frozen=True)
class Solved:
    portfolio_id: str
    strategy: str
    trades: pd.DataFrame  # portfolio_id, cusip, lot_id (sells only), side, par
    positions: pd.DataFrame  # cusip, par, weight after trading
    summary: dict[str, float]  # objective, and gap for integer solves; value, cash, turnover, tax, cost, income in $; tracking as L1 weight distance
    violations: dict[str, float]  # constraint -> worst excess in weights, at the exact trades
    seconds: float

    @property
    def ok(self) -> bool:
        """Safe to trade: every constraint holds at the exact trades."""
        return not self.violations

    @property
    def status(self) -> str:
        return "ok" if self.ok else f"rounding broke {sorted(self.violations)}"

    @classmethod
    def from_model(cls, m: Model, *, portfolio_id: str, strategy: str, objective: float, gap: float | None, violations: dict[str, float], seconds: float) -> Self:
        """Read the result off a model whose variables hold the exact trades."""
        return cls(portfolio_id, strategy, _trades(m, portfolio_id), _positions(m), _summary(m, objective, gap), violations, seconds)


@dataclass(frozen=True)
class Unsolved:
    portfolio_id: str
    strategy: str
    status: str  # the solver's status ("infeasible; before any trade it breaks ...", "user_limit") or the error that stopped the solve


type Result = Solved | Unsolved


def _trades(m: Model, portfolio_id: str) -> pd.DataFrame:
    buy, sell = value_of(m.buy), value_of(m.sell)
    buys = pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[buy > 0], "side": "buy", "par": buy[buy > 0]})
    sells = pd.DataFrame({"cusip": m.lots["cusip"].to_numpy()[sell > 0], "lot_id": m.lots["lot_id"].to_numpy()[sell > 0], "side": "sell", "par": sell[sell > 0]})
    trades = pd.concat([buys, sells], ignore_index=True).assign(portfolio_id=portfolio_id)
    return trades.astype({"portfolio_id": "str", "cusip": "str", "lot_id": "str", "side": "str", "par": "int64"})[["portfolio_id", "cusip", "lot_id", "side", "par"]]


def _positions(m: Model) -> pd.DataFrame:
    par = value_of(m.par)
    held = par > 0.5
    return pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[held], "par": par[held].round().astype("int64"), "weight": value_of(m.weights)[held]})


def _summary(m: Model, objective: float, gap: float | None) -> dict[str, float]:
    return {
        "objective": objective,  # the solver's optimum before snapping or rounding, as a fraction of value
        **({"gap": gap} if gap is not None else {}),  # how far the integer optimum may be from the best possible
        "value": m.value,
        "cash": float(value_of(m.cash)),
        "turnover": float(value_of(m.turnover)),
        "tax": float(value_of(m.tax)),
        "cost": float(value_of(m.cost)),
        "income": float(value_of(m.income)),
        "tracking": float(np.abs(value_of(m.weights) - m.target).sum()) + m.untracked,
    }
