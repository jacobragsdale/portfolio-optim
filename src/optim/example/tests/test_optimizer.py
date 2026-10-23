"""The optimizer, on a few bonds: what each kind of constraint holds a solve to, and what `build` refuses."""

from dataclasses import replace

import optim
import pytest
from pydantic import ValidationError

from optim.example import model
from optim.example.optimizer import problem, solve
from optim.example.tests.builders import params, portfolio

ROOM = {"kind": "turnover", "max": 1.0}
"""Turnover enough to buy anything."""


def decide(found: model.Portfolio, *constraints: dict[str, object]) -> optim.Decision:
    return solve.solve(replace(found, constraints=[ROOM, *constraints]), params())


def test_a_weight_cap_per_group_caps_each_group() -> None:
    """Each bond is worth 1 a unit, and the portfolio 1,000,000."""
    found = portfolio({"security_id": "A", "issuer": "X"}, {"security_id": "B", "issuer": "X"}, {"security_id": "C", "issuer": "Y"})

    decision = decide(found, {"kind": "weight", "per": "issuer", "max": 0.1})

    assert decision.solved
    assert decision.buy["A"] + decision.buy.get("B", 0.0) == pytest.approx(100_000.0)
    assert decision.buy["C"] == pytest.approx(100_000.0)


def test_a_weight_cap_where_a_column_matches_caps_those_bonds_together() -> None:
    found = portfolio({"security_id": "A", "sector": "Financials"}, {"security_id": "B", "sector": "Financials"}, {"security_id": "C", "sector": "Utilities"})

    decision = decide(found, {"kind": "weight", "per": "security_id", "max": 0.3}, {"kind": "weight", "where": {"sector": "Financials"}, "max": 0.04})

    assert decision.buy.get("A", 0.0) + decision.buy.get("B", 0.0) == pytest.approx(40_000.0)
    assert decision.buy["C"] == pytest.approx(300_000.0)


def test_a_cap_leaves_the_portfolio_a_holding_it_may_not_sell() -> None:
    """A is 20% of the portfolio, over its cap of 10%, but may not be sold: it stays, and nothing more is bought."""
    found = portfolio({"security_id": "A", "held": 250_000.0}, cash=1_000_000.0)
    found = replace(found, universe=found.universe.assign(sellable=False))

    decision = decide(found, {"kind": "weight", "per": "security_id", "max": 0.1})

    assert decision.solved
    assert decision.buy == {}
    assert decision.sell == {}


def test_a_definition_build_does_not_know_fails_naming_the_kinds_it_knows() -> None:
    found = portfolio({"security_id": "A"})

    with pytest.raises(ValidationError, match="'weight', 'turnover'"):
        problem.build(found.universe, [{"kind": "wieght", "max": 0.1}], found.objective, value=found.value)
