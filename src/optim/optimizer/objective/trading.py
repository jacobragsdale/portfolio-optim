"""What trading costs: estimated costs, and penalties on how much is traded."""

from typing import ClassVar, Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import OptimizationModel, Scoped
from optim.schema import StrictModel


class Cost(StrictModel):
    """Estimated trading costs."""

    kind: Literal["cost"] = "cost"

    def expression(self, m: OptimizationModel) -> cp.Expression:
        cost: cp.Expression = -m.cost / m.base
        return cost


class TurnoverQuadratic(Scoped):
    """Trading, penalized by the square of each line's traded weight: one large trade costs more than several small ones
    of the same total. Quadratic."""

    kind: Literal["turnover_quadratic"] = "turnover_quadratic"
    penalty: float = Field(gt=0)
    """Per unit of summed squared weight traded."""
    quadratic: ClassVar[bool] = True

    def expression(self, m: OptimizationModel) -> cp.Expression:
        scope = m.scope(self.sleeve)
        traded = cp.multiply(scope.members * m.unit_value / scope.base, m.buy + m.sell)
        turnover: cp.Expression = -self.penalty * cp.sum_squares(traded)
        return turnover
