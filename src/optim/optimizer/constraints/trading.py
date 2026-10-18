"""How much the portfolio trades."""

from typing import Literal

import cvxpy as cp

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel, Weight


class MaxTurnover(StrictModel):
    """Trade at most `max_weight` of the portfolio's value."""

    kind: Literal["max_turnover"] = "max_turnover"
    max_weight: Weight

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        return [m.turnover / m.value <= self.max_weight]
