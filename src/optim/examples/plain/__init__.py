"""The plain example: a model-portfolio rebalance decided in plain Python, with no solver. Every portfolio trades
toward the same target weights, from its params, and sells what the targets leave out. The README beside this module
states its behaviour in business terms."""

import optim
from pydantic import Field

from optim.examples.plain import rules


class Params(optim.Params):
    targets: dict[str, float] = Field(default_factory=dict)
    """Each security's target weight of the portfolio's value, by security id; what the targets leave is cash."""
    tolerance: float = Field(default=0.002, ge=0)
    """A holding within this weight of its target is left alone, to save trading costs."""


class Data(optim.Datasets):
    pass


class Metrics(optim.Metrics):
    drift: float
    """How far the portfolio's weights are from the targets before trading: the sum of the absolute differences."""


def decide(prepared: Data, shared: Data, params: Params) -> optim.Decision:  # noqa: ARG001 -- the hook's signature
    holdings, securities = prepared.holdings, prepared.securities
    weights = rules.weights(holdings, securities)
    value, prices = rules.worth(holdings, securities), rules.prices(securities)
    buy, sell = rules.toward(weights, params.targets, value=value, prices=prices, tolerance=params.tolerance)
    return optim.Decision(buy=buy, sell=sell, metrics=Metrics(drift=rules.drift(weights, params.targets)))
