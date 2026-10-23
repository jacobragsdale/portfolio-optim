"""The model's rules, on a few rows each: where a portfolio is, and the trades that bring it to its targets."""

import pytest
from optim.datasets import HOLDINGS, SECURITIES
from optim.testing import CASH, frame

from optim.examples.plain import rules

SECURITIES_ = frame(SECURITIES, CASH, {"security_id": "A"}, {"security_id": "B", "price": 50.0})
HOLDINGS_ = frame(HOLDINGS, {"quantity": 500_000.0}, {"security_id": "A", "quantity": 300_000.0}, {"security_id": "B", "quantity": 400_000.0})  # $1mm: 30% A, 20% B


def test_weights_are_of_the_whole_portfolio_with_cash_aside() -> None:
    assert rules.weights(HOLDINGS_, SECURITIES_).to_dict() == {"A": 0.3, "B": 0.2}


def test_trades_go_toward_the_targets_and_sell_what_they_leave_out() -> None:
    weights = rules.weights(HOLDINGS_, SECURITIES_)

    buy, sell = rules.toward(weights, {"B": 0.25}, value=1_000_000.0, prices=rules.prices(SECURITIES_), tolerance=0.002)

    assert buy.to_dict() == {"B": pytest.approx(100_000.0)}  # $50k more of B, at 50
    assert sell.to_dict() == {"A": pytest.approx(300_000.0)}  # A is not a target: sold whole


def test_a_holding_within_tolerance_of_its_target_is_left_alone() -> None:
    buy, sell = rules.toward(rules.weights(HOLDINGS_, SECURITIES_), {"A": 0.301, "B": 0.2}, value=1_000_000.0, prices=rules.prices(SECURITIES_), tolerance=0.002)

    assert buy.empty
    assert sell.empty


def test_drift_is_how_far_the_weights_are_from_the_targets() -> None:
    assert rules.drift(rules.weights(HOLDINGS_, SECURITIES_), {"A": 0.3, "C": 0.1}) == pytest.approx(0.3)
