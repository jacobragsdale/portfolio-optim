"""The model's rules, on a few rows each: what a portfolio may buy and sell, and what the model's check rejects."""

from collections.abc import Mapping

import optim
import pandas as pd
import pytest
from optim.datasets import HOLDINGS, SECURITIES
from optim.testing import CASH, frame

from optim.examples.cvxpy import rules
from optim.examples.cvxpy.datasets import CONTROLS, SIGNALS

SECURITIES_ = frame(SECURITIES, CASH, {"security_id": "A"}, {"security_id": "B", "issuer": "Issuer B", "rating": "BBB"}, {"security_id": "C", "issuer": "Issuer C"})
MARKET = rules.market(SECURITIES_, frame(SIGNALS, {"security_id": "A", "expected_return": 0.05}, {"security_id": "B", "expected_return": 0.06}))


def lines(*holdings: Mapping[str, object], controls: tuple[Mapping[str, object], ...] = ()) -> pd.DataFrame:
    held = rules.lines(frame(HOLDINGS, {}, *holdings), MARKET)
    applied = rules.apply_controls(held, frame(CONTROLS, *controls, default={"portfolio_id": "P1", "control_id": "R1", "reason": "test"}))
    return applied.set_index("security_id")[["held", "buyable", "sellable", "must_sell"]]


def test_a_portfolio_may_buy_every_bond_with_a_signal_and_sell_what_it_holds() -> None:
    """C has no signal: it is held, so it is a line, but it cannot be bought."""
    assert lines({"security_id": "C", "quantity": 5000.0}).to_dict("index") == {
        "C": {"held": 5000.0, "buyable": False, "sellable": True, "must_sell": False},
        "A": {"held": 0.0, "buyable": True, "sellable": False, "must_sell": False},
        "B": {"held": 0.0, "buyable": True, "sellable": False, "must_sell": False},
    }


def test_controls_apply_and_a_control_naming_no_security_applies_to_every_one() -> None:
    controls = ({"security_id": "A", "action": "must_sell"}, {"control_id": "R2", "security_id": None, "action": "no_buy"})

    applied = lines({"security_id": "A", "quantity": 5000.0}, controls=controls)

    assert applied.loc["A"].to_dict() == {"held": 5000.0, "buyable": False, "sellable": True, "must_sell": True}
    assert not applied.loc["B", "buyable"]


def test_controls_that_both_require_and_forbid_a_sale_fail_the_portfolio() -> None:
    with pytest.raises(ValueError, match=r"both require and forbid selling \['A'\]"):
        lines({"security_id": "A", "quantity": 5000.0}, controls=({"security_id": "A", "action": "must_sell"}, {"control_id": "R2", "security_id": "A", "action": "hold"}))


def test_the_rating_floor_and_the_restricted_list_narrow_what_may_be_bought() -> None:
    held = rules.lines(frame(HOLDINGS, {}), MARKET)

    assert rules.min_rating(held, "A-").set_index("security_id")["buyable"].to_dict() == {"A": True, "B": False}
    assert rules.exclude_issuers(held, ["Issuer A"]).set_index("security_id")["buyable"].to_dict() == {"A": False, "B": True}


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (optim.Decision(buy={"A": 1500.0}), "buys A in a size the market does not trade"),
        (optim.Decision(buy={"A": 60_000.0}), "over its limit of 5.00%"),
        (optim.Decision(sell={"C": 1000.0}), "trades C, which it neither holds nor may buy"),
    ],
    ids=["size", "position-limit", "not-sellable"],
)
def test_the_check_rejects_what_breaks_the_models_rules(decision: optim.Decision, reason: str) -> None:
    held = rules.lines(frame(HOLDINGS, {}), MARKET)

    reasons = rules.violations(held, 1_000_000.0, decision, max_weight=0.05, max_issuer_weight=0.1, max_turnover=0.25)

    assert any(reason in found for found in reasons), reasons
