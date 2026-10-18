"""Which bonds may be bought: credit, issuer, maturity, and a cap on how many candidates reach the solver."""

from typing import Literal, Self

import pandas as pd
from pydantic import Field, model_validator

from optim.inputs import SolverInputs
from optim.schema import Rating, StrictModel


class MinRating(StrictModel):
    """Buy only bonds rated `rating` or better."""

    kind: Literal["min_rating"] = "min_rating"
    rating: Rating

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        return inputs.restrict_buys(inputs.securities["rating"] <= self.rating)


class ExcludeIssuers(StrictModel):
    """Never buy these issuers (restricted list, credit watch)."""

    kind: Literal["exclude_issuers"] = "exclude_issuers"
    issuers: list[str]

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        return inputs.restrict_buys(~inputs.securities["issuer"].isin(self.issuers))


class MaturityRange(StrictModel):
    """Buy only bonds maturing between `min_years` and `max_years` from today."""

    kind: Literal["maturity_range"] = "maturity_range"
    min_years: float = Field(default=0, ge=0)
    max_years: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_years > self.max_years:
            msg = f"min_years {self.min_years} is above max_years {self.max_years}"
            raise ValueError(msg)
        return self

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        years = (inputs.securities["maturity"] - pd.Timestamp(inputs.as_of_date)).dt.days / 365.25
        return inputs.restrict_buys(years.between(self.min_years, self.max_years))


class TopSignals(StrictModel):
    """Keep only the `n` best buy candidates by expected return, to bound the solve size."""

    kind: Literal["top_signals"] = "top_signals"
    n: int = Field(gt=0)

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        s = inputs.securities
        return inputs.restrict_buys(s["expected_return"].where(s["buyable"]).rank(ascending=False, method="first") <= self.n)
