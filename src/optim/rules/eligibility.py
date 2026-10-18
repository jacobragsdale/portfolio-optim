"""Which securities may be bought: credit and issuer."""

from typing import Literal

from optim.inputs import SolverInputs
from optim.schema import Rating, StrictModel


class MinRating(StrictModel):
    """Buy only securities rated `rating` or better."""

    kind: Literal["min_rating"] = "min_rating"
    rating: Rating

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        return inputs.restrict_buys(inputs.lines["rating"] <= self.rating)


class ExcludeIssuers(StrictModel):
    """Never buy these issuers (restricted list, credit watch)."""

    kind: Literal["exclude_issuers"] = "exclude_issuers"
    issuers: list[str]

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        return inputs.restrict_buys(~inputs.lines["issuer"].isin(self.issuers))
