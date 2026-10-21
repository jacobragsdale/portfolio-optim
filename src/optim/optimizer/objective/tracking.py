"""Distance from the scope's benchmark: in weights, or in exposures."""

from typing import ClassVar, Literal

import cvxpy as cp
from pydantic import Field

from optim.datasets.securities import Analytic
from optim.optimizer.model import OptimizationModel, Scoped


class TrackingL1(Scoped):
    """Distance from the scope's benchmark: the L1 norm of the weight differences. Adds a variable and two rows per line."""

    kind: Literal["tracking_l1"] = "tracking_l1"
    penalty: float = Field(gt=0)
    """Per unit of L1 distance."""

    def expression(self, m: OptimizationModel) -> cp.Expression:
        scope = m.scope(self.sleeve)
        tracking: cp.Expression = -self.penalty * cp.norm1(m.weights(scope) - m.target(scope))
        return tracking


class ExposureTracking(Scoped):
    """Distance from the benchmark's exposures: the squared difference of each analytic's exposure from the benchmark's,
    summed. Quadratic."""

    kind: Literal["exposure_tracking"] = "exposure_tracking"
    columns: list[Analytic] = Field(min_length=1)
    penalty: float = Field(gt=0)
    """Per unit of summed squared difference, in the analytics' units."""
    quadratic: ClassVar[bool] = True

    def expression(self, m: OptimizationModel) -> cp.Expression:
        scope = m.scope(self.sleeve)
        differences = [m.exposure(scope, column) - m.benchmark_exposure(scope, column) for column in self.columns]
        tracking: cp.Expression = -self.penalty * cp.sum_squares(cp.hstack(differences))
        return tracking
