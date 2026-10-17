"""How much the portfolio trades, and how much it keeps in cash."""

from typing import Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import Model
from optim.schema import Strict, Weight


class MaxTurnover(Strict):
    """Trade at most `max_weight` of the portfolio's value."""

    kind: Literal["max_turnover"] = "max_turnover"
    max_weight: Weight

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.turnover / m.value <= self.max_weight]


class CashRange(Strict):
    """Keep cash between `min_weight` and `max_weight` of the portfolio."""

    kind: Literal["cash_range"] = "cash_range"
    min_weight: Weight = 0.0
    max_weight: Weight = 1.0

    def build(self, m: Model) -> list[cp.Constraint]:
        return [m.cash / m.value >= self.min_weight, m.cash / m.value <= self.max_weight]


class MaxBuys(Strict):
    """Buy at most `n` different bonds. It counts the model's 0/1 `buying` flags, so it needs an integer solve."""

    kind: Literal["max_buys"] = "max_buys"
    n: int = Field(ge=0)

    def build(self, m: Model) -> list[cp.Constraint]:
        if m.buying is None:
            msg = 'max_buys counts bonds bought, which needs "solve": {"integer": true}'
            raise ValueError(msg)
        return [cp.sum(m.buying) <= self.n]
