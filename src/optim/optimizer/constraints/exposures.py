"""Exposure limits: a scope's exposure to an analytic, such as duration, on its own or against its benchmark's."""

from typing import Literal, Self

import cvxpy as cp
from pydantic import model_validator

from optim.datasets.securities import Analytic
from optim.optimizer.model import OptimizationModel, Scoped


class ExposureLimit(Scoped):
    """Keep the scope's exposure to `column` within `min` and `max`, in the column's units: with `relative`, the default,
    its difference from the benchmark's exposure, such as duration within half a year of the benchmark's. An exposure is
    each line's weight times its value of the column, summed, so cash counts as 0."""

    kind: Literal["exposure"] = "exposure"
    column: Analytic
    relative: bool = True
    """Bound the exposure less the scope's benchmark's; false: the exposure itself."""
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.min is None and self.max is None:
            msg = "an exposure limit needs min, max or both"
            raise ValueError(msg)
        if self.min is not None and self.max is not None and self.min > self.max:
            msg = f"min {self.min} is above max {self.max}"
            raise ValueError(msg)
        return self

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        scope = m.scope(self.sleeve)
        exposure = m.exposure(scope, self.column)
        measured = exposure - m.benchmark_exposure(scope, self.column) if self.relative else exposure
        return [*([measured >= self.min] if self.min is not None else []), *([measured <= self.max] if self.max is not None else [])]
