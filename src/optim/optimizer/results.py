"""What a solve returns: `Solved`, with trades, positions and a summary rebuilt from the exact trades and the checks that
decide whether it may trade (`acceptance`), or `Unsolved`. A snapshot saves every result as the frames `result_frames`
makes, one row model each: `Trade`, `Position`, `Summary`, `Violation`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from optim.data.frames import conform
from optim.schema import Cusip, Id, Quantity, Row


@dataclass(frozen=True)
class Solved:
    portfolio_id: str
    strategy: str
    trades: pd.DataFrame  # `Trade` columns but `strategy`
    positions: pd.DataFrame  # `Position` columns but `portfolio_id` and `strategy`
    summary: dict[str, object]  # `Summary`'s fields after `status`, but `seconds`
    violations: dict[str, float]  # each failed acceptance check -> how far it is broken
    seconds: float

    @property
    def accepted(self) -> bool:
        """May trade: every acceptance check passed. A solver's optimum alone is not enough."""
        return not self.violations

    @property
    def status(self) -> str:
        return "accepted" if self.accepted else f"rejected: {sorted(self.violations)}"


@dataclass(frozen=True)
class Unsolved:
    portfolio_id: str
    strategy: str
    status: str  # why: the solver's stop ("infeasible; before any trade it breaks ...", "limit with no solution") or the error that stopped the solve


type Result = Solved | Unsolved


class Trade(Row):
    portfolio_id: Id
    strategy: str
    sleeve_id: Id
    security_id: Id
    cusip: Cusip | None
    side: Literal["buy", "sell"]
    quantity: Quantity
    """Exact: whole denominations, or for a sale the whole holding as booked."""
    value: float
    """Quantity times the security's unit value, $: `(price + accrued) / price_per` each."""
    liquidates: bool
    """A sale of the whole holding."""
    mandatory: bool
    """A sale a `must_sell` control required."""


class Position(Row):
    """A line held after trading: one sleeve's holding of one security, cash aside."""

    portfolio_id: Id
    strategy: str
    sleeve_id: Id
    security_id: Id
    quantity: Quantity
    """Exact: as held, plus buys, less sales."""
    value: float
    """$, at the security's unit value."""
    weight: float
    """Value over the portfolio's base value."""


class Summary(Row):
    """One per solve. Its numbers are null when the solve failed. Dollar amounts are rebuilt from the exact trades."""

    portfolio_id: Id
    strategy: str
    status: str
    """`accepted`; `rejected: [...]`, naming the failed checks; or why it did not solve."""
    stop: str | None
    """Why the solver stopped: `optimal`, `limit` (the time limit, unless options set another), or `imprecise`."""
    objective: float | None
    """The solver's optimum before snapping or rounding, as a fraction of base value."""
    gap: float | None
    """Integer solves: how far the optimum may be from the best possible, relative, as the solver reports it."""
    nodes: int | None
    """Integer solves: branch-and-bound nodes explored."""
    value: float | None
    """Pre-trade value, cash included, before cash flows, $."""
    base: float | None
    """The value weights are fractions of: the target value, or the value after cash flows, $."""
    cash_flow: float | None
    """Cash flows applied, $: positive in, negative out."""
    cash: float | None
    """Cash after trading and cash flows, $."""
    turnover: float | None
    """Value bought plus value sold, $."""
    cost: float | None
    """Estimated trading costs, $."""
    income: float | None
    """Expected return over a year after trading, $."""
    tracking: float | None
    """L1 distance of the weights from the portfolio's benchmark, including constituents outside the model; null without one."""
    seconds: float | None
    """Time to build, solve and review the portfolio."""


class Violation(Row):
    """An acceptance check a result failed (`optimizer.acceptance`). The result is saved, and the run exits nonzero."""

    portfolio_id: Id
    strategy: str
    check: str
    """`sizes`, `controls`, `cash`, `model`, `solver`, or a constraint by its place in the strategy and its kind, such as
    `3:max_turnover`."""
    excess: float
    """How far it is broken: trades or lines for `sizes` and `controls`, the gap for `solver`, weights for the rest."""


def result_frames(results: Sequence[Result]) -> dict[str, pd.DataFrame]:
    """The results as a snapshot saves them: a row per trade, position, solve and failed check."""
    solved = [r for r in results if isinstance(r, Solved)]
    failed = [{"portfolio_id": r.portfolio_id, "strategy": r.strategy, "check": name, "excess": excess} for r in solved for name, excess in r.violations.items()]
    return {
        "trades": conform(Trade, [r.trades.assign(strategy=r.strategy) for r in solved], name="trades"),
        "positions": conform(Position, [r.positions.assign(portfolio_id=r.portfolio_id, strategy=r.strategy) for r in solved], name="positions"),
        "summary": conform(Summary, [pd.DataFrame([_summary_row(r) for r in results])], name="summary"),
        "violations": conform(Violation, [pd.DataFrame(failed)], name="violations"),
    }


def _summary_row(result: Result) -> dict[str, object]:
    match result:
        case Solved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status, **result.summary, "seconds": result.seconds}
        case Unsolved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status}
