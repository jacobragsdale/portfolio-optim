"""Pre-solve rules. Each is a pure function of `Inputs`: it narrows what may be bought (`buyable`) or adjusts what the
solver believes (`expected_return`). Rows are never dropped, because a bond you may not buy can still be held and sold.

Rules do no I/O. A rule that needs more data gets a dataset in `inputs.py` and a column on the securities frame.
To add a rule: write a class here and add it to `Rule`. Its fields are its JSON parameters.
"""

from dataclasses import replace
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from optim.data import Strict
from optim.inputs import Inputs, Rating


def _restrict(inputs: Inputs, allowed: pd.Series) -> Inputs:
    securities = inputs.securities
    return replace(inputs, securities=securities.assign(buyable=securities["buyable"] & allowed))


class MinRating(Strict):
    """Buy only bonds rated `rating` or better."""

    kind: Literal["min_rating"] = "min_rating"
    rating: Rating

    def apply(self, inputs: Inputs) -> Inputs:
        return _restrict(inputs, inputs.securities["rating"] <= self.rating)


class ExcludeIssuers(Strict):
    """Never buy these issuers (restricted list, credit watch)."""

    kind: Literal["exclude_issuers"] = "exclude_issuers"
    issuers: tuple[str, ...]

    def apply(self, inputs: Inputs) -> Inputs:
        return _restrict(inputs, ~inputs.securities["issuer"].isin(self.issuers))


class MaturityRange(Strict):
    """Buy only bonds maturing between `min_years` and `max_years` from today."""

    kind: Literal["maturity_range"] = "maturity_range"
    min_years: float = Field(default=0, ge=0)
    max_years: float = Field(gt=0)

    def apply(self, inputs: Inputs) -> Inputs:
        years = (inputs.securities["maturity"] - pd.Timestamp(inputs.as_of)).dt.days / 365.25
        return _restrict(inputs, years.between(self.min_years, self.max_years))


class AfterTax(Strict):
    """Turn pre-tax expected returns into this account's after-tax returns: munis are free of federal tax, and of
    state tax in the account's own state; taxable bonds pay both."""

    kind: Literal["after_tax"] = "after_tax"

    def apply(self, inputs: Inputs) -> Inputs:
        a, s = inputs.account, inputs.securities
        muni = s["tax_status"] == "muni"
        in_state = muni & (s["state"] == a.state)
        rate = np.select([in_state, muni], [0.0, a.state_rate], default=a.federal_rate + a.state_rate)
        return replace(inputs, securities=s.assign(expected_return=s["expected_return"] * (1 - rate)))


class NoWashSales(Strict):
    """Don't buy a bond the account holds at a loss: selling that lot for the loss and buying it back within 30 days
    would disallow the loss."""

    kind: Literal["no_wash_sales"] = "no_wash_sales"

    def apply(self, inputs: Inputs) -> Inputs:
        priced = inputs.lots.merge(inputs.securities[["cusip", "price"]], on="cusip", how="left", validate="many_to_one")
        at_loss = priced.loc[priced["price"] < priced["cost_price"], "cusip"]
        # ponytail: open lots only; add a recent-sales dataset to also block buys within 30 days after a loss sale
        return _restrict(inputs, ~inputs.securities["cusip"].isin(at_loss))


class TopSignals(Strict):
    """Keep only the `n` best buy candidates by expected return, to bound the solve size."""

    kind: Literal["top_signals"] = "top_signals"
    n: int = Field(gt=0)

    def apply(self, inputs: Inputs) -> Inputs:
        s = inputs.securities
        rank = s["expected_return"].where(s["buyable"]).rank(ascending=False, method="first")
        return _restrict(inputs, rank <= self.n)


Rule = Annotated[MinRating | ExcludeIssuers | MaturityRange | AfterTax | NoWashSales | TopSignals, Field(discriminator="kind")]
