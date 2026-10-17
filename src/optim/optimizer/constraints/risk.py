"""Risk relative to the target."""

from typing import Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import Model
from optim.schema import Strict


class DurationBand(Strict):
    """Keep portfolio duration within `tolerance` years of the target's."""

    kind: Literal["duration_band"] = "duration_band"
    tolerance: float = Field(gt=0)

    def build(self, m: Model) -> list[cp.Constraint]:
        gap = m.securities["duration"].to_numpy() @ m.weights - m.target_duration
        return [gap <= self.tolerance, gap >= -self.tolerance]
