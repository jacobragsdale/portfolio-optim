"""What a solve returns: `Solved`, with trades, positions and a summary at tradable lots, or `Unsolved`. A snapshot saves
every result as the frames `result_frames` makes, one row model each: `Trade`, `Position`, `Summary`, `Violation`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self

import numpy as np
import pandas as pd

from optim.data.frames import conform
from optim.optimizer.model import OptimizationModel, value_of
from optim.schema import Cusip, Par, Row


@dataclass(frozen=True)
class Solved:
    portfolio_id: str
    strategy: str
    trades: pd.DataFrame  # portfolio_id, cusip, lot_id (sells only), side, par
    positions: pd.DataFrame  # cusip, par, weight after trading
    summary: dict[str, float]  # `Summary`'s numbers: objective, gap for integer solves, value, cash, turnover, tax, cost, income, tracking
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
    def from_model(cls, m: OptimizationModel, *, portfolio_id: str, strategy: str, objective: float, gap: float | None, violations: dict[str, float], seconds: float) -> Self:
        """Read the result off a model whose variables hold the exact trades."""
        return cls(portfolio_id, strategy, _trades(m, portfolio_id), _positions(m), _summary(m, objective, gap), violations, seconds)


@dataclass(frozen=True)
class Unsolved:
    portfolio_id: str
    strategy: str
    status: str  # the solver's status ("infeasible; before any trade it breaks ...", "user_limit") or the error that stopped the solve


type Result = Solved | Unsolved


class Trade(Row):
    portfolio_id: str
    strategy: str
    cusip: Cusip
    lot_id: str | None  # the tax lot a sale comes from; none for a buy
    side: Literal["buy", "sell"]
    par: Par


class Position(Row):
    """A bond held after trading."""

    portfolio_id: str
    strategy: str
    cusip: Cusip
    par: Par
    weight: float  # of pre-trade value, cash included


class Summary(Row):
    """One per solve. Its numbers are null when the solve failed."""

    portfolio_id: str
    strategy: str
    status: str  # `ok`; what rounding broke; or why it did not solve
    objective: float | None  # the solver's optimum before snapping or rounding, as a fraction of value
    gap: float | None  # integer solves: how far the optimum may be from the best possible
    value: float | None  # pre-trade value including cash, $
    cash: float | None  # after trading, $
    turnover: float | None  # value traded, $
    tax: float | None  # on realized gains, $; negative when harvesting losses
    cost: float | None  # trading costs, $
    income: float | None  # expected after-tax return, $ per year
    tracking: float | None  # L1 distance of the weights from the target
    seconds: float | None


class Violation(Row):
    """A constraint broken at the exact trades, as after rounding an LP. The result is saved, and the run exits nonzero."""

    portfolio_id: str
    strategy: str
    constraint: str  # as the solve names it: its place in the strategy and its kind, such as `3:max_turnover`
    excess: float  # the worst excess, in weights


def result_frames(results: Sequence[Result]) -> dict[str, pd.DataFrame]:
    """The results as a snapshot saves them: a row per trade, position, solve and broken constraint."""
    solved = [r for r in results if isinstance(r, Solved)]
    broken = [{"portfolio_id": r.portfolio_id, "strategy": r.strategy, "constraint": name, "excess": excess} for r in solved for name, excess in r.violations.items()]
    return {
        "trades": conform(Trade, [r.trades.assign(strategy=r.strategy) for r in solved], name="trades"),
        "positions": conform(Position, [r.positions.assign(portfolio_id=r.portfolio_id, strategy=r.strategy) for r in solved], name="positions"),
        "summary": conform(Summary, [pd.DataFrame([_summary_row(r) for r in results])], name="summary"),
        "violations": conform(Violation, [pd.DataFrame(broken)], name="violations"),
    }


def _summary_row(result: Result) -> dict[str, object]:
    match result:
        case Solved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status, **result.summary, "seconds": result.seconds}
        case Unsolved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status}


def _trades(m: OptimizationModel, portfolio_id: str) -> pd.DataFrame:
    buy, sell = value_of(m.buy), value_of(m.sell)
    buys = pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[buy > 0], "side": "buy", "par": buy[buy > 0]})
    sells = pd.DataFrame({"cusip": m.lots["cusip"].to_numpy()[sell > 0], "lot_id": m.lots["lot_id"].to_numpy()[sell > 0], "side": "sell", "par": sell[sell > 0]})
    trades = pd.concat([buys, sells], ignore_index=True).assign(portfolio_id=portfolio_id)
    return trades.astype({"portfolio_id": "str", "cusip": "str", "lot_id": "str", "side": "str", "par": "int64"})[["portfolio_id", "cusip", "lot_id", "side", "par"]]


def _positions(m: OptimizationModel) -> pd.DataFrame:
    par = value_of(m.par)
    held = par > 0.5
    return pd.DataFrame({"cusip": m.securities["cusip"].to_numpy()[held], "par": par[held].round().astype("int64"), "weight": value_of(m.weights)[held]})


def _summary(m: OptimizationModel, objective: float, gap: float | None) -> dict[str, float]:
    return {
        "objective": objective,
        **({"gap": gap} if gap is not None else {}),
        "value": m.value,
        "cash": float(value_of(m.cash)),
        "turnover": float(value_of(m.turnover)),
        "tax": float(value_of(m.tax)),
        "cost": float(value_of(m.cost)),
        "income": float(value_of(m.income)),
        "tracking": float(np.abs(value_of(m.weights) - m.target).sum()) + m.untracked,
    }
