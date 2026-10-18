"""What the optimizer maximizes: the sum of the strategy's objective terms. Each term returns what it adds to the
objective as a fraction of portfolio value, so terms share one scale; a penalty returns a negative amount.

`Term` is the union that strategy JSON is validated against, so every term class must be added to it.
"""

from typing import Annotated, Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel


class ExpectedReturn(StrictModel):
    """Expected income over the horizon, as prep and the rules left `expected_return`."""

    kind: Literal["expected_return"] = "expected_return"
    horizon_years: float = Field(default=1.0, gt=0)

    def expression(self, m: OptimizationModel) -> cp.Expression:
        income: cp.Expression = self.horizon_years * m.income / m.value
        return income


class Tax(StrictModel):
    """Tax the trades realize. Harvested losses count as negative tax, so they add to the objective."""

    kind: Literal["tax"] = "tax"
    aversion: float = Field(default=1.0, ge=0)
    """1 means a dollar of tax costs a dollar of return."""

    def expression(self, m: OptimizationModel) -> cp.Expression:
        tax: cp.Expression = -self.aversion * m.tax / m.value
        return tax


class Cost(StrictModel):
    """Estimated trading costs."""

    kind: Literal["cost"] = "cost"

    def expression(self, m: OptimizationModel) -> cp.Expression:
        cost: cp.Expression = -m.cost / m.value
        return cost


class TrackingL1(StrictModel):
    """Distance from the target: the L1 norm of the weight differences. Adds a variable and two rows per security."""

    kind: Literal["tracking_l1"] = "tracking_l1"
    penalty: float = Field(gt=0)
    """Per unit of L1 distance."""

    def expression(self, m: OptimizationModel) -> cp.Expression:
        tracking: cp.Expression = -self.penalty * cp.norm1(m.weights - m.target)
        return tracking


# TODO(port): the old system's objective, its penalties and preferences, are term classes here, added to this union.
Term = Annotated[ExpectedReturn | Tax | Cost | TrackingL1, Field(discriminator="kind")]
