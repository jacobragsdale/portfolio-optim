"""What the optimizer maximizes: the sum of the strategy's objective terms. Each term returns what it adds to the
objective as a fraction of base value, so terms share one scale; a penalty returns a negative amount.

A quadratic term sets `quadratic`: HiGHS solves it only without integers, so a strategy that pairs one with an integer
HiGHS solve is refused when it is read (`Strategy`), never solved with a linear stand-in.

`Term` is the union that strategy JSON is validated against, so every term class must be added to it.
"""

from typing import Annotated, ClassVar, Literal, Self

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydantic import Field, model_validator

from optim.datasets.securities import Analytic
from optim.optimizer.model import OptimizationModel, Scoped
from optim.schema import StrictModel


class ExpectedReturn(StrictModel):
    """Expected income over the horizon, as prep and the rules left `expected_return`."""

    kind: Literal["expected_return"] = "expected_return"
    horizon_years: float = Field(default=1.0, gt=0)

    def expression(self, m: OptimizationModel) -> cp.Expression:
        income: cp.Expression = self.horizon_years * m.income / m.base
        return income


class Cost(StrictModel):
    """Estimated trading costs."""

    kind: Literal["cost"] = "cost"

    def expression(self, m: OptimizationModel) -> cp.Expression:
        cost: cp.Expression = -m.cost / m.base
        return cost


class TrackingL1(Scoped):
    """Distance from the scope's benchmark: the L1 norm of the weight differences. Adds a variable and two rows per line."""

    kind: Literal["tracking_l1"] = "tracking_l1"
    penalty: float = Field(gt=0)
    """Per unit of L1 distance."""

    def expression(self, m: OptimizationModel) -> cp.Expression:
        scope = m.scope(self.sleeve)
        tracking: cp.Expression = -self.penalty * cp.norm1(m.weights(scope) - m.target(scope))
        return tracking


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


# TODO(port): the old system's objective, its penalties and preferences, are term classes here, added to this union.
Term = Annotated[ExpectedReturn | Cost | TrackingL1 | TurnoverQuadratic | ExposureTracking | LadderBalance, Field(discriminator="kind")]
