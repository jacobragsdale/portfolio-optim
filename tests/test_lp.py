"""The LP's decisions on tiny portfolios whose right answer is obvious."""

from optim.lp import TOLERANCE, CashRange, Objective, PositionLimit, Solved, solve
from optim.outputs import orders_for
from tests.builders import account, inputs, lot, security, tables


def test_sells_the_loss_lot_and_keeps_the_gain_lot() -> None:
    """Two lots of one bond: selling the one bought above today's price saves tax, selling the other costs tax."""
    data = tables([security(1), security(2)], accounts=[account()], lots=[lot(1, "GAIN", cost_price=90.0), lot(1, "LOSS", cost_price=110.0)])
    result = solve(inputs(data), Objective(), [], strategy="test")
    assert isinstance(result, Solved)
    assert result.ok
    assert result.trades.loc[result.trades["side"] == "sell", ["lot_id", "par"]].to_dict("records") == [{"lot_id": "LOSS", "par": 100_000}]


def test_position_limit_sells_down_in_whole_increments() -> None:
    data = tables([security(1), security(2), security(3)], accounts=[account()], lots=[lot(1, "L1", par=1_000_000)])
    result = solve(inputs(data), Objective(), [PositionLimit(max_weight=0.4)], strategy="test")
    assert isinstance(result, Solved)
    assert result.ok
    assert result.positions["weight"].max() <= 0.4 + TOLERANCE
    assert (result.trades["par"] % 5000 == 0).all()


def test_a_result_that_rounding_breaks_is_flagged_and_never_ordered() -> None:
    """$1mm of cash, $5k lots, and a 0.1% cash cap: whole lots leave ~0.4% in cash."""
    data = tables([security(1)], accounts=[account(cash=1_000_000.0)])
    result = solve(inputs(data), Objective(), [CashRange(max_weight=0.001)], strategy="test")
    assert isinstance(result, Solved)
    assert list(result.violations) == ["0:cash_range"]
    assert orders_for("run", [result]) == []
