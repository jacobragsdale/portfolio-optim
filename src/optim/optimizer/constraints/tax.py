"""Taxes the trades may realize."""

from typing import Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel


class MaxTax(StrictModel):
    """Realize at most `dollars` of tax."""

    kind: Literal["max_tax"] = "max_tax"
    dollars: float = Field(ge=0)

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        return [m.tax / m.value <= self.dollars / m.value]
