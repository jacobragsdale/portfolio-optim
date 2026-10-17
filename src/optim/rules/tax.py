"""Rules that follow from the account's taxes."""

from dataclasses import replace
from typing import Literal

import numpy as np

from optim.inputs import Inputs
from optim.schema import Strict


class AfterTax(Strict):
    """Turn pre-tax expected returns into this account's after-tax returns: munis are free of federal tax, and of state
    tax in the account's own state; taxable bonds pay both."""

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
        return inputs.restrict(~inputs.securities["cusip"].isin(at_loss))
