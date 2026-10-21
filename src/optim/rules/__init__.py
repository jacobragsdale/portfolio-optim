"""Pre-solve rules: the choices a strategy makes. Each is a pure function of `SolverInputs`: it narrows what may be bought
or sold, by line (`SolverInputs.restrict_buys`, `restrict_sales`), or adjusts what the solver believes
(`expected_return`). Rules do no I/O; a rule that needs more data gets a dataset and a column. What every strategy must
obey, such as a portfolio's controls, is prep, so a strategy that leaves a rule out cannot trade around it.

`Rule` is the union that strategy JSON is validated against, so every rule class must be added to it.
"""

import logging
import time
from collections.abc import Sequence
from typing import Annotated

import pandas as pd
from pydantic import Field

from optim.logs import ms_since, timed
from optim.prep import SolverInputs
from optim.rules.eligibility import ExcludeIssuers, MinRating

log = logging.getLogger(__name__)

# TODO(port): the old system's rules are rule classes, in a module here per subject, added to this union.
Rule = Annotated[MinRating | ExcludeIssuers, Field(discriminator="kind")]


def apply_rules(rules: Sequence[Rule], inputs: SolverInputs) -> SolverInputs:
    """Each rule in the strategy's order, logging how long each took. At DEBUG, also logs what each one changed."""
    with timed(log, "applied rules", portfolio_id=inputs.portfolio.details.portfolio_id) as fields:
        rule_ms: dict[str, float] = {}  # keyed as the solve names constraints: position in the strategy, then kind
        fields["rule_ms"] = rule_ms
        for i, rule in enumerate(rules):
            start = time.perf_counter()
            inputs = _apply(rule, inputs)
            rule_ms[f"{i}:{rule.kind}"] = ms_since(start)
    return inputs


def _apply(rule: Rule, inputs: SolverInputs) -> SolverInputs:
    after = rule.apply(inputs)
    if changed := [name for name, frame in _frames(after).items() if not frame.index.equals(_frames(inputs)[name].index)]:
        # a dropped line hides a holding from the solve, or leaves a benchmark constituent unpriced
        msg = f"rule {rule.kind} changed the rows of {', '.join(changed)}: a rule narrows `buyable` or `sellable`, or edits `expected_return`, and keeps every row"
        raise ValueError(msg)
    if log.isEnabledFor(logging.DEBUG):  # the diff reads every line
        was, now = inputs.lines, after.lines
        dropped = was.loc[was["buyable"] & ~now["buyable"], "security_id"]
        log.debug(
            "rule applied",
            extra={
                "portfolio_id": inputs.portfolio.details.portfolio_id,
                "rule": rule,
                "buyable": int(now["buyable"].sum()),
                "dropped": len(dropped),
                "dropped_examples": dropped.head(20).tolist(),
                "sellable": int(now["sellable"].sum()),
                "returns_changed": int((was["expected_return"] != now["expected_return"]).sum()),
            },
        )
    return after


def _frames(inputs: SolverInputs) -> dict[str, pd.DataFrame]:
    return {"lines": inputs.lines, "securities": inputs.securities} | {name: value for name, value in vars(inputs.portfolio).items() if isinstance(value, pd.DataFrame)}
