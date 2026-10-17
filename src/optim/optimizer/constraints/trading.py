"""How much the portfolio trades, and how much it keeps in cash."""

from typing import Literal

import cvxpy as cp

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
