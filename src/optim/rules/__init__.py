"""Pre-solve rules. Each is a pure function of `SolverInputs`: it narrows what may be bought (`SolverInputs.restrict`) or adjusts
what the solver believes (`expected_return`). Rules do no I/O; a rule that needs more data gets a dataset and a column.

`Rule` is the union that strategy JSON is validated against, so every rule class must be added to it.
"""

import logging
from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from optim.inputs import SolverInputs
from optim.rules.eligibility import ExcludeIssuers, MaturityRange, MinRating, TopSignals
from optim.rules.tax import AfterTax, NoWashSales

log = logging.getLogger(__name__)

Rule = Annotated[MinRating | ExcludeIssuers | MaturityRange | TopSignals | AfterTax | NoWashSales, Field(discriminator="kind")]


def apply_rules(rules: Sequence[Rule], inputs: SolverInputs) -> SolverInputs:
    """Each rule in the strategy's order. At DEBUG, logs what each one changed."""
    for rule in rules:
        inputs = _apply(rule, inputs)
    return inputs


def _apply(rule: Rule, inputs: SolverInputs) -> SolverInputs:
    after = rule.apply(inputs)
    was, now = inputs.securities, after.securities
    dropped = was.loc[was["buyable"] & ~now["buyable"], "cusip"]
    changed = int((was["expected_return"] != now["expected_return"]).sum())
    log.debug(
        "rule applied",
        extra={
            "portfolio_id": inputs.portfolio.details.portfolio_id,
            "rule": rule,
            "buyable": int(now["buyable"].sum()),
            "dropped": len(dropped),
            "dropped_examples": dropped.head(20).tolist(),
            "returns_changed": changed,
        },
    )
    return after
