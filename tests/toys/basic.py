"""A toy model for the core's tests: it buys 1,000 of a security in every portfolio, and fails, or does not solve,
for the portfolios its params name. Its check rejects a buy of `BAD`."""

from pydantic import Field

import optim


class Params(optim.Params):
    buy: str = "A"
    fail: list[str] = Field(default_factory=list)
    """Portfolios whose decision raises."""
    unsolved: list[str] = Field(default_factory=list)
    """Portfolios whose decision is not solved."""


class Data(optim.Datasets):
    pass


def decide(prepared: Data, shared: Data, params: Params) -> optim.Decision:  # noqa: ARG001
    if prepared.portfolio_id in params.fail:
        msg = f"cannot decide {prepared.portfolio_id}"
        raise ValueError(msg)
    return optim.Decision(buy={params.buy: 1000.0}, solved=prepared.portfolio_id not in params.unsolved, metrics={"holdings": len(prepared.holdings)})


def check(prepared: Data, decision: optim.Decision) -> list[str]:  # noqa: ARG001
    return ["buys BAD"] if "BAD" in decision.buy else []
