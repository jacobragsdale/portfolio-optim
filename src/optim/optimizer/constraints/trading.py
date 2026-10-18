"""How much the portfolio trades, and how much it keeps in cash."""

from typing import Literal, Self

import cvxpy as cp
from pydantic import Field, model_validator

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel, Weight


class MaxTurnover(StrictModel):
    """Trade at most `max_weight` of the portfolio's value."""

    kind: Literal["max_turnover"] = "max_turnover"
    max_weight: Weight

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        return [m.turnover / m.value <= self.max_weight]


class CashRange(StrictModel):
    """Keep cash between `min_weight` and `max_weight` of the portfolio."""

    kind: Literal["cash_range"] = "cash_range"
    min_weight: Weight = 0.0
    max_weight: Weight = 1.0

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_weight > self.max_weight:
            msg = f"min_weight {self.min_weight} is above max_weight {self.max_weight}"
            raise ValueError(msg)
        return self

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        return [m.cash / m.value >= self.min_weight, m.cash / m.value <= self.max_weight]


class MaxBuys(StrictModel):
    """Buy at most `n` different bonds. It counts the model's 0/1 `buying` flags, so it needs an integer solve."""

    kind: Literal["max_buys"] = "max_buys"
    n: int = Field(ge=0)

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        if m.buying is None:
            msg = 'max_buys counts bonds bought, which needs "solve": {"integer": true}'
            raise ValueError(msg)
        return [cp.sum(m.buying) <= self.n]
