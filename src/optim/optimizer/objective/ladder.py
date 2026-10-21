"""How a scope's weight is spread across maturities."""

from typing import ClassVar, Literal, Self

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydantic import Field, model_validator

from optim.optimizer.model import OptimizationModel, Scoped


class LadderBalance(Scoped):
    """An even ladder: the weight maturing in each year from `first_year` to `last_year` near their mean, penalized by
    the squared differences. A line maturing in 1.2 years is in year 2's rung; one outside the ladder, or without a
    maturity, is in none. Quadratic."""

    kind: Literal["ladder_balance"] = "ladder_balance"
    first_year: int = Field(default=1, ge=1)
    last_year: int = Field(ge=1)
    penalty: float = Field(gt=0)
    """Per unit of summed squared weight difference."""
    quadratic: ClassVar[bool] = True

    @model_validator(mode="after")
    def _a_ladder(self) -> Self:
        if self.last_year <= self.first_year:
            msg = f"last_year {self.last_year} must be after first_year {self.first_year}"
            raise ValueError(msg)
        return self

    def expression(self, m: OptimizationModel) -> cp.Expression:
        scope = m.scope(self.sleeve)
        years = ((m.lines["maturity"] - pd.Timestamp(m.inputs.as_of_date)).dt.days / 365.25).to_numpy(dtype=float, na_value=np.nan)
        rungs = np.arange(self.first_year, self.last_year + 1)
        in_rung = (np.ceil(years)[None, :] == rungs[:, None]) & scope.members  # rung x line
        weights = sp.csr_array(in_rung.astype(float)) @ m.weights(scope)
        imbalance: cp.Expression = -self.penalty * cp.sum_squares(weights - cp.sum(weights) / len(rungs))
        return imbalance
