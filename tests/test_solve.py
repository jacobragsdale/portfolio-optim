"""What one solve does with the inputs prep and its rules leave it, and when its result may trade."""

import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from builders import CASH, USD, A, B, C, frame, market, portfolio, prepare

from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, PORTFOLIOS, SLEEVES
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.optimizer.acceptance import review
from optim.optimizer.backends import Termination
from optim.optimizer.constraints.exposures import ExposureLimit
from optim.optimizer.constraints.limits import PositionLimit
from optim.optimizer.constraints.trading import MaxTrades, MaxTurnover
from optim.optimizer.model import build_model
from optim.optimizer.objective import Cost, ExpectedReturn, ExposureTracking, LadderBalance, Term, TurnoverQuadratic
from optim.optimizer.results import Result, Solved
from optim.optimizer.solve import solve
from optim.prep import SolverInputs
from optim.strategy import SolveSettings, Strategy

INTEGER_AND_LP = pytest.mark.parametrize("settings", [SolveSettings(integer=True), SolveSettings(integer=False)], ids=["integer", "lp"])
BONDS = [{"security_id": A}, {"security_id": B, "cusip": B, "issuer": "Issuer B", "duration": 2.0}, {"security_id": C, "cusip": C, "issuer": "Issuer C"}]
EVERY_BOND = {
    "securities": frame(SECURITIES, *BONDS, CASH),
    "universe": frame(UNIVERSE, {"security_id": A}, {"security_id": B}, {"security_id": C}),
    "signals": frame(SIGNALS, {"security_id": A, "expected_return": 0.05}, {"security_id": B, "expected_return": 0.04}, {"security_id": C, "expected_return": 0.03}),
}


def holding_b(quantity: int = 100_500, *, b_return: float = 0.03, **frames: pd.DataFrame) -> SolverInputs:
    """P1 holds an odd 100,500 of bond B, outside its list, and $1mm cash; its list is bond A, which returns 5%."""
    signals = frame(SIGNALS, {"security_id": A, "expected_return": 0.05}, {"security_id": B, "expected_return": b_return})
    held = frame(HOLDINGS, {}, {"security_id": B, "quantity": float(quantity)})
    return prepare(market(securities=frame(SECURITIES, *BONDS, CASH), signals=signals) | portfolio(holdings=held) | frames)


def accepted(result: Result) -> Solved:
    assert isinstance(result, Solved), result
    assert result.accepted, result.violations
    return result


def trades(result: Solved) -> list[tuple[str, str, float, bool, bool]]:
    return [(t["side"], t["security_id"], t["quantity"], t["liquidates"], t["mandatory"]) for t in result.trades.to_dict("records")]


def number(result: Solved, field: str) -> float:
    value = result.summary[field]
    assert isinstance(value, float | int), value
    return value


@INTEGER_AND_LP
def test_a_holding_is_sold_by_security_and_an_odd_one_whole_exactly(settings: SolveSettings) -> None:
    result = accepted(solve(holding_b(), Strategy(name="s", solve=settings)))

    assert trades(result)[-1] == ("sell", B, 100_500.0, True, False)
    assert trades(result)[0][:2] == ("buy", A)
    assert result.trades["quantity"].iloc[0] % 1000 == 0


@INTEGER_AND_LP
def test_a_mandatory_sale_is_sold_whole_whatever_the_strategy_prefers_or_its_rules_say(settings: SolveSettings) -> None:
    """Bond B returns more than A, so the strategy would keep it; a control says sell it."""
    inputs = holding_b(b_return=0.08, controls=frame(CONTROLS, {"security_id": B, "action": "must_sell", "reason": "downgrade"}))
    restricted = inputs.restrict_sales(pd.Series(data=False, index=inputs.lines.index))  # a rule that blocks every sale

    result = accepted(solve(restricted, Strategy(name="s", solve=settings)))

    assert ("sell", B, 100_500.0, True, True) in trades(result)


@INTEGER_AND_LP
def test_a_discretionary_sale_block_keeps_a_holding_the_strategy_would_sell(settings: SolveSettings) -> None:
    inputs = holding_b(controls=frame(CONTROLS, {"security_id": B, "action": "no_sell", "reason": "client_restriction"}))

    result = accepted(solve(inputs, Strategy(name="s", solve=settings)))

    assert "sell" not in result.trades["side"].tolist()


def test_sleeves_share_the_cash_and_each_weighs_against_its_own_base() -> None:
    """A credit sleeve targets 60% of the portfolio and a Treasury sleeve 40%, each with its own list and benchmark. Capped
    at half its sleeve, a credit bond may be at most 30% of the portfolio; what credit cannot take goes to Treasuries."""
    treasury = {"security_id": "T", "cusip": None, "issuer": "US TREASURY", "sector": "Treasury", "rating": "AAA", "duration": 2.0}
    frames = market(
        securities=frame(SECURITIES, *BONDS, treasury, CASH),
        universe=frame(UNIVERSE, {"universe_id": "credit", "security_id": A}, {"universe_id": "credit", "security_id": C}, {"universe_id": "treasury", "security_id": "T"}),
        signals=frame(SIGNALS, {"security_id": A, "expected_return": 0.05}, {"security_id": C, "expected_return": 0.04}, {"security_id": "T", "expected_return": 0.02}),
        benchmarks=frame(BENCHMARKS, {"benchmark_id": "credit", "security_id": A}, {"benchmark_id": "treasury", "security_id": "T"}),
    ) | portfolio(
        sleeves=frame(
            SLEEVES,
            {"sleeve_id": "credit", "universe_id": "credit", "benchmark_id": "credit", "target_weight": 0.6},
            {"sleeve_id": "treasury", "universe_id": "treasury", "benchmark_id": "treasury", "target_weight": 0.4},
        ),
        holdings=frame(HOLDINGS, {"sleeve_id": "credit"}),
    )
    strategy = Strategy(name="s", constraints=[PositionLimit(sleeve="credit", max_weight=0.5)])

    result = accepted(solve(prepare(frames), strategy))

    bought = result.trades.set_index("security_id")
    assert bought.loc[[A, C], "sleeve_id"].tolist() == ["credit", "credit"]
    assert bought.loc["T", "sleeve_id"] == "treasury"
    assert (bought.loc[[A, C], "value"] <= 300_000).all()
    assert bought.loc[[A, C], "value"].sum() > 590_000
    assert 0 <= number(result, "cash") < 1_000


def test_inventory_bounds_a_securitys_buys_across_its_sleeves() -> None:
    """One list, with 100,000 of A available, in two sleeves: together they buy at most 100,000."""
    frames = market(universe=frame(UNIVERSE, {"available": 100_000.0})) | portfolio(
        sleeves=frame(SLEEVES, {"sleeve_id": "s1", "target_weight": 0.5}, {"sleeve_id": "s2", "target_weight": 0.5}), holdings=frame(HOLDINGS, {"sleeve_id": "s1"})
    )

    for settings in (SolveSettings(integer=True), SolveSettings(integer=False)):
        result = accepted(solve(prepare(frames), Strategy(name="s", solve=settings)))
        assert result.trades["quantity"].sum() == 100_000


def test_a_portfolio_benchmark_with_no_weight_is_refused_rather_than_tracked() -> None:
    """A newly funded portfolio's only sleeve has no target weight and holds only cash, so it has no share of the
    portfolio: the portfolio's benchmark weighs nothing."""
    frames = market() | portfolio(sleeves=frame(SLEEVES, {"target_weight": None}))
    strategy = Strategy(name="s", constraints=[ExposureLimit(column="duration", min=-0.25, max=0.25)])

    with pytest.raises(ValueError, match="scope portfolio of portfolio P1 has no benchmark"):
        solve(prepare(frames), strategy)


def test_a_strategy_naming_a_sleeve_the_portfolio_lacks_fails() -> None:
    with pytest.raises(LookupError, match="has no sleeve 'treasury'"):
        solve(holding_b(), Strategy(name="s", constraints=[MaxTurnover(sleeve="treasury", max_weight=0.1)]))


def test_an_overdrawn_cash_balance_is_raised_by_selling() -> None:
    inputs = prepare(market() | portfolio(holdings=frame(HOLDINGS, {"quantity": -50_000.0}, {"security_id": A})))

    result = accepted(solve(inputs, Strategy(name="s")))

    assert trades(result) == [("sell", A, 51_000.0, False, False)]
    assert 0 <= number(result, "cash") < 1_000


def test_a_portfolio_with_nothing_to_trade_is_reviewed_without_a_solve() -> None:
    """A blanket hold freezes the portfolio: no trades, and cash short of a withdrawal is still caught."""
    frozen = {"holdings": frame(HOLDINGS, {}, {"security_id": A}), "controls": frame(CONTROLS, {"security_id": None, "action": "hold", "reason": "legal_hold"})}

    still = solve(prepare(market() | portfolio(**frozen)), Strategy(name="s"))
    short = solve(prepare(market() | portfolio(**frozen, cash_flows=frame(CASH_FLOWS, {"amount": -1_500_000.0}))), Strategy(name="s"))

    assert accepted(still).trades.empty
    assert isinstance(short, Solved)
    assert set(short.violations) == {"cash"}


def test_a_withdrawal_is_raised_by_selling() -> None:
    inputs = prepare(market() | portfolio(holdings=frame(HOLDINGS, {"security_id": A}), cash_flows=frame(CASH_FLOWS, {"amount": -200_000.0})))

    result = accepted(solve(inputs, Strategy(name="s")))

    assert trades(result) == [("sell", A, 201_000.0, False, False)]  # whole increments, enough for the cost too
    assert result.summary["base"] == 800_000
    assert 0 <= number(result, "cash") < 1_000


def test_weights_are_fractions_of_the_target_value_where_one_is_set() -> None:
    """$1mm cash, managed as a $2mm portfolio: a 10% position is $200k."""
    frames = market(**EVERY_BOND) | portfolio(portfolios=frame(PORTFOLIOS, {"target_value": 2_000_000.0}))

    result = accepted(solve(prepare(frames), Strategy(name="s", constraints=[PositionLimit(max_weight=0.1)])))

    assert result.positions["value"].tolist() == [200_000, 200_000, 200_000]
    assert result.summary["base"] == 2_000_000


def test_quadratic_terms_solve_as_a_qp_on_highs() -> None:
    """Bond A (duration 4) returns more than B (duration 2), and the benchmark holds half of each: tracking the benchmark's
    duration of 3 pulls the portfolio away from all A."""
    frames = (
        market(
            securities=frame(SECURITIES, *BONDS, CASH),
            universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}),
            signals=frame(SIGNALS, {"security_id": A, "expected_return": 0.05}, {"security_id": B, "expected_return": 0.03}),
            benchmarks=frame(BENCHMARKS, {"security_id": A, "weight": 0.5}, {"security_id": B, "weight": 0.5}),
        )
        | portfolio()
    )
    lp = SolveSettings(integer=False)
    terms: list[Term] = [ExpectedReturn(), Cost(), TurnoverQuadratic(penalty=0.001), LadderBalance(first_year=1, last_year=5, penalty=0.001)]

    loose = accepted(solve(prepare(frames), Strategy(name="s", objective=terms, solve=lp)))
    tracked = accepted(solve(prepare(frames), Strategy(name="s", objective=[*terms, ExposureTracking(columns=["duration"], penalty=1.0)], solve=lp)))

    assert loose.summary["stop"] == tracked.summary["stop"] == "optimal"
    assert _duration(loose) > 3.9
    assert _duration(tracked) == pytest.approx(3.0, abs=0.05)


def test_copt_solves_a_quadratic_objective_with_integer_trades() -> None:
    pytest.importorskip("coptpy")
    frames = (
        market(
            securities=frame(SECURITIES, *BONDS, CASH),
            universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}),
            benchmarks=frame(BENCHMARKS, {"security_id": A, "weight": 0.5}, {"security_id": B, "weight": 0.5}),
        )
        | portfolio()
    )
    strategy = Strategy(name="s", objective=[ExpectedReturn(), ExposureTracking(columns=["duration"], penalty=1.0)], solve=SolveSettings(solver="COPT", integer=True))

    result = accepted(solve(prepare(frames), strategy))

    assert result.summary["stop"] == "optimal"
    assert result.summary["gap"] is not None
    assert result.summary["nodes"] is not None
    assert (result.trades["quantity"] % 1000 == 0).all()
    assert _duration(result) == pytest.approx(3.0, abs=0.05)


def test_an_exposure_limit_holds_duration_near_the_benchmarks() -> None:
    frames = (
        market(
            securities=frame(SECURITIES, *BONDS, CASH),
            universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}),
            benchmarks=frame(BENCHMARKS, {"security_id": A, "weight": 0.5}, {"security_id": B, "weight": 0.5}),
        )
        | portfolio()
    )
    strategy = Strategy(name="s", constraints=[ExposureLimit(column="duration", min=-0.1, max=0.1)])

    result = accepted(solve(prepare(frames), strategy))

    assert 2.9 - 1e-6 <= _duration(result) <= 3.1 + 1e-6


def test_max_trades_caps_the_number_of_trades() -> None:
    frames = market(**EVERY_BOND) | portfolio()
    strategy = Strategy(name="s", constraints=[PositionLimit(max_weight=0.3), MaxTrades(n=2)])

    result = accepted(solve(prepare(frames), strategy))

    assert len(result.trades) == 2


def test_lp_rounding_that_breaks_a_binding_constraint_is_rejected() -> None:
    """An LP sells $450 of B to buy $449 of A within a 0.9% turnover cap; rounded, the sale is a $1,000 piece and the buy
    nothing, which trades 1%."""
    inputs = holding_b(b_return=0.01, holdings=frame(HOLDINGS, {"security_id": B, "quantity": 100_000.0}))  # and no cash
    strategy = Strategy(name="s", constraints=[MaxTurnover(max_weight=0.009)], solve=SolveSettings(integer=False))

    result = solve(inputs, strategy)

    assert isinstance(result, Solved)
    assert result.status == "rejected: ['0:max_turnover']"


OPTIMAL = Termination("optimal", "test", has_solution=True, gap=None, nodes=None)


@pytest.mark.parametrize(
    ("buy", "change", "termination", "failed"),
    [
        (1_000, None, OPTIMAL, set()),
        (1_500, None, OPTIMAL, {"sizes"}),
        (1_000, "not_buyable", OPTIMAL, {"controls"}),
        (1_200_000, None, OPTIMAL, {"cash", "0:max_turnover"}),  # an overspend is over a limit too
        (1_000, "cash_off", OPTIMAL, {"model"}),
        (1_000, None, Termination("limit", "test", has_solution=True, gap=0.05, nodes=10), {"solver"}),
        (1_000, None, Termination("optimal", "test", has_solution=True, gap=0.05, nodes=10), {"solver"}),  # optimal under a looser gap option
        (1_000, None, Termination("limit", "test", has_solution=True, gap=0.005, nodes=10), set()),
        (1_000, None, Termination("imprecise", "test", has_solution=True, gap=None, nodes=None), {"solver"}),
        (20_000, None, OPTIMAL, {"0:max_turnover"}),
    ],
    ids=["fine", "sizes", "controls", "cash", "model", "limit-past-gap", "optimal-past-gap", "limit-within-gap", "imprecise", "constraint"],
)
def test_review_rejects_what_may_not_trade_whatever_the_solver_says(buy: int, change: str | None, termination: Termination, failed: set[str]) -> None:
    """P1 holds B and $1mm, and may buy A. Each case sets a buy of A directly, as a solver would, or bends the model."""
    m = build_model(holding_b(100_000), integer=False)
    if change == "not_buyable":  # the model says A is buyable; the inputs, which review checks against, say not
        m = replace(m, lines=m.lines.assign(buyable=False))
    if change == "cash_off":  # a formulation that loses $2 of a $1.1mm portfolio's cash
        m = replace(m, cash=m.cash - 2.0)
    assert m.lp is not None
    m.lp.buy.value, m.lp.sell.value = np.array([buy]) / m.lp.per_weight[m.lp.to_buy], np.array([0.0])
    constraints = {"0:max_turnover": MaxTurnover(max_weight=0.01).build(m)}

    result = review(m, constraints, termination, strategy="s", objective=0.0, max_gap=0.01, start=time.perf_counter())

    assert set(result.violations) == failed


def _duration(result: Solved) -> float:
    durations = {A: 4.0, B: 2.0}
    weights = result.positions.set_index("security_id")["weight"]
    return float(sum(weights[s] * durations[s] for s in weights.index if s != USD))
