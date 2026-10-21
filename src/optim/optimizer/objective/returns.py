"""What a strategy expects to earn."""

from typing import Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel


class ExpectedReturn(StrictModel):
    """Expected income over the horizon, as prep and the rules left `expected_return`."""

    kind: Literal["expected_return"] = "expected_return"
    horizon_years: float = Field(default=1.0, gt=0)

    def expression(self, m: OptimizationModel) -> cp.Expression:
        income: cp.Expression = self.horizon_years * m.income / m.base
        return income
