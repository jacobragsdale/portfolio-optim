"""What the optimizer maximizes."""

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import Model
from optim.schema import Strict


class Objective(Strict):
    """Expected after-tax return over the horizon, net of taxes and trading costs, less a penalty on distance from the
    target. Measured as a fraction of portfolio value."""

    horizon_years: float = Field(default=1.0, gt=0)
    tax_aversion: float = Field(default=1.0, ge=0)  # 1: a dollar of tax costs a dollar of return
    tracking_penalty: float = Field(default=0.0, ge=0)  # per unit of L1 distance between weights and target

    def expression(self, m: Model) -> cp.Expression:
        tracking = cp.norm1(m.weights - m.target) if self.tracking_penalty else 0.0  # L1 adds a variable and two rows per security
        net: cp.Expression = (self.horizon_years * m.income - self.tax_aversion * m.tax - m.cost) / m.value - self.tracking_penalty * tracking
        return net
