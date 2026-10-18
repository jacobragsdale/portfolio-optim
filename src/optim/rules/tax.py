"""Rules that follow from the portfolio's taxes."""

from dataclasses import replace
from typing import Literal

import numpy as np

from optim.inputs import SolverInputs
from optim.schema import StrictModel


class AfterTax(StrictModel):
    """Turn pre-tax expected returns into this portfolio's after-tax returns: interest pays federal tax, and state tax
    unless the bond is state-exempt, as Treasuries are."""

    kind: Literal["after_tax"] = "after_tax"

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        p, s = inputs.portfolio, inputs.securities
        rate = np.where(s["tax_status"] == "state_exempt", p.federal_rate, p.federal_rate + p.state_rate)
        return replace(inputs, securities=s.assign(expected_return=s["expected_return"] * (1 - rate)))


class NoWashSales(StrictModel):
    """Don't buy a bond the portfolio holds at a loss, or sold at a loss in the last 30 days: a buy within 30 days of a
    loss sale, before or after it, disallows the loss."""

    kind: Literal["no_wash_sales"] = "no_wash_sales"

    def apply(self, inputs: SolverInputs) -> SolverInputs:
        priced = inputs.lots.merge(inputs.securities[["cusip", "price"]], on="cusip", how="left", validate="many_to_one")
        held_at_loss = priced.loc[priced["price"] < priced["cost_price"], "cusip"]
        # blocks buys only; a loss sold within 30 days after buying the bond is a wash sale too, and the tax
        # term still counts it. Zero the loss on lots of bonds with a lot acquired in the last 30 days if that matters.
        return inputs.restrict(~inputs.securities["cusip"].isin([*held_at_loss, *inputs.loss_sales["cusip"]]))
