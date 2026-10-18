"""What one solve does with the inputs its rules leave it."""

import pytest
from builders import AS_OF, A, B, frame, market

from optim.datasets.portfolios import LOSS_SALES, LOTS, PORTFOLIOS
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.inputs import SolverInputs, build_market, build_portfolios
from optim.optimizer.results import Solved
from optim.optimizer.solve import solve
from optim.strategy import SolveSettings, Strategy


def harvest() -> SolverInputs:
    """P1 holds bond B, bought at 104 and now at a clean 99.5, with cash to buy bond A: selling B harvests a loss."""
    frames = market(
        securities=frame(SECURITIES, {"cusip": A}, {"cusip": B, "issuer": "Issuer B"}),
        signals=frame(SIGNALS, {"cusip": A, "expected_return": 0.05}, {"cusip": B, "expected_return": 0.03}),
        universe=frame(UNIVERSE, {"cusip": A}),
    ) | {"portfolios": frame(PORTFOLIOS, {}), "lots": frame(LOTS, {"cusip": B, "cost_price": 104.0}), "loss_sales": frame(LOSS_SALES)}
    [portfolio] = build_portfolios(frames, ["P1"])
    return build_market(frames, AS_OF).inputs_for(portfolio)


@pytest.mark.parametrize("settings", [SolveSettings(integer=True), SolveSettings(integer=False)], ids=["integer", "lp"])
def test_a_lot_is_sold_until_a_rule_forbids_it(settings: SolveSettings) -> None:
    strategy, inputs = Strategy(name="s", solve=settings), harvest()

    sold = solve(inputs, strategy)
    kept = solve(inputs.restrict_sales(inputs.portfolio.lots["lot_id"] != "L1"), strategy)

    assert isinstance(sold, Solved)
    assert isinstance(kept, Solved)
    assert sold.trades.loc[sold.trades["side"] == "sell", "lot_id"].tolist() == ["L1"]
    assert "sell" not in kept.trades["side"].tolist()
