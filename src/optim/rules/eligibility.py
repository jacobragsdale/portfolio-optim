"""Which bonds may be bought: credit, issuer, maturity, and a cap on how many candidates reach the solver."""

from typing import Literal

import pandas as pd
from pydantic import Field

from optim.inputs import Inputs
from optim.schema import Rating, Strict


class MinRating(Strict):
    """Buy only bonds rated `rating` or better."""

    kind: Literal["min_rating"] = "min_rating"
    rating: Rating

    def apply(self, inputs: Inputs) -> Inputs:
        return inputs.restrict(inputs.securities["rating"] <= self.rating)


class ExcludeIssuers(Strict):
    """Never buy these issuers (restricted list, credit watch)."""

    kind: Literal["exclude_issuers"] = "exclude_issuers"
    issuers: list[str]

    def apply(self, inputs: Inputs) -> Inputs:
        return inputs.restrict(~inputs.securities["issuer"].isin(self.issuers))


class MaturityRange(Strict):
    """Buy only bonds maturing between `min_years` and `max_years` from today."""

    kind: Literal["maturity_range"] = "maturity_range"
    min_years: float = Field(default=0, ge=0)
    max_years: float = Field(gt=0)

    def apply(self, inputs: Inputs) -> Inputs:
        years = (inputs.securities["maturity"] - pd.Timestamp(inputs.as_of)).dt.days / 365.25
        return inputs.restrict(years.between(self.min_years, self.max_years))


class TopSignals(Strict):
    """Keep only the `n` best buy candidates by expected return, to bound the solve size."""

    kind: Literal["top_signals"] = "top_signals"
    n: int = Field(gt=0)

    def apply(self, inputs: Inputs) -> Inputs:
        s = inputs.securities
        return inputs.restrict(s["expected_return"].where(s["buyable"]).rank(ascending=False, method="first") <= self.n)
