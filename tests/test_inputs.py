"""How the datasets a run loads become what its solves see: the same prep live and on replay."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from builders import ALL_DATASETS, AS_OF, CASH, OPTIMIZE, STRATEGY, USD, A, B, C, context, frame, market, portfolio, prepare, sources
from pandas.testing import assert_frame_equal

from optim.data.dataset import Dataset
from optim.data.frames import empty_frame
from optim.datasets import MARKET
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, PORTFOLIOS, SLEEVES, Holding
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.jobs.optimize import OptimizeRequest
from optim.jobs.run import ReplayRequest, RunInfo, replay, run
from optim.optimizer.results import Solved
from optim.outputs.snapshots import Code, load_run, save_run
from optim.prep.market import build_market
from optim.prep.portfolios import build_portfolios
from optim.storage import Root

THREE_BONDS = frame(SECURITIES, {"security_id": A}, {"security_id": B, "cusip": B}, {"security_id": C, "cusip": None, "isin": "XS0000000009"}, CASH)


def test_builder_rows_pass_their_dataset_schemas() -> None:
    for dataset in ALL_DATASETS:
        assert len(frame(dataset, {})) == 1


def test_lines_are_every_holding_but_cash_and_every_eligible_security() -> None:
    """P1 holds B, which is not on its list, and cash; the list is A and C, a security known by its ISIN alone, with no
    inventory for A and 5mm of C."""
    frames = market(securities=THREE_BONDS, universe=frame(UNIVERSE, {"security_id": A}, {"security_id": C, "available": 5_000_000.0})) | portfolio(
        holdings=frame(HOLDINGS, {}, {"security_id": B, "quantity": 250000.00}), cash_flows=frame(CASH_FLOWS, {"amount": -50000.50})
    )

    inputs = prepare(frames)

    assert inputs.lines[["security_id", "held", "buyable", "sellable", "available"]].to_dict("records") == [
        {"security_id": A, "held": 0, "buyable": True, "sellable": False, "available": pytest.approx(np.nan, nan_ok=True)},
        {"security_id": B, "held": 250000.00, "buyable": False, "sellable": True, "available": pytest.approx(np.nan, nan_ok=True)},
        {"security_id": C, "held": 0, "buyable": True, "sellable": False, "available": 5_000_000.0},
    ]
    assert (inputs.cash, inputs.cash_flow) == (1_000_000.0, -50_000.5)  # cash holdings pooled; flows summed


def test_a_security_in_another_currency_cannot_be_bought_and_holding_one_fails_the_portfolio() -> None:
    securities = frame(SECURITIES, {}, {"security_id": B, "cusip": B, "currency": "EUR"}, CASH)
    listed = market(securities=securities, universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}))

    assert prepare(listed | portfolio()).lines.set_index("security_id")["buyable"].to_dict() == {A: True, B: False}
    with pytest.raises(ValueError, match="another currency than its USD"):
        prepare(listed | portfolio(holdings=frame(HOLDINGS, {}, {"security_id": B})))


def test_controls_apply_whatever_the_strategy() -> None:
    """No rules here: a portfolio's controls are prep. A control naming no security applies to every one."""
    held = frame(HOLDINGS, {}, {"security_id": A, "quantity": 10_000.0}, {"security_id": B, "quantity": 10_000.0}, {"security_id": C, "quantity": 10_000.0})
    controls = frame(
        CONTROLS,
        {"control_id": "R1", "security_id": A, "action": "no_sell", "reason": "client_restriction"},
        {"control_id": "R2", "security_id": B, "action": "must_sell", "reason": "downgrade"},
        {"control_id": "R3", "security_id": None, "action": "no_buy", "reason": "account_closing"},
    )
    listed = market(securities=THREE_BONDS, universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}, {"security_id": C}))

    lines = prepare(listed | portfolio(holdings=held, controls=controls)).lines.set_index("security_id")

    assert lines[["buyable", "sellable", "must_sell"]].to_dict("index") == {
        A: {"buyable": False, "sellable": False, "must_sell": False},
        B: {"buyable": False, "sellable": True, "must_sell": True},
        C: {"buyable": False, "sellable": True, "must_sell": False},
    }


@pytest.mark.parametrize(
    ("controls", "error"),
    [
        ([{"control_id": "R1", "action": "must_sell", "reason": "downgrade"}, {"control_id": "R2", "action": "hold", "reason": "legal_hold"}], "both require and forbid selling"),
        (
            [{"control_id": "R1", "security_id": None, "action": "must_sell", "reason": "termination"}, {"control_id": "R2", "action": "no_sell", "reason": "client"}],
            r"R2 no_sell AAAAAAAA1 \(client\)",
        ),
        ([{"control_id": "R1", "security_id": "UNKNOWN", "action": "no_sell", "reason": "client"}], "name securities that are not loaded"),
    ],
    ids=["must-sell-and-hold", "liquidation-and-no-sell", "unknown-security"],
)
def test_controls_that_could_silently_stop_applying_fail_the_portfolio(controls: list[dict[str, object]], error: str) -> None:
    frames = market(securities=THREE_BONDS) | portfolio(holdings=frame(HOLDINGS, {}, {"security_id": A}), controls=frame(CONTROLS, *controls))

    with pytest.raises((ValueError, LookupError), match=error):
        prepare(frames)


def test_a_mandatory_sale_of_a_security_no_longer_held_is_done_and_still_stops_buying_it_back() -> None:
    """In a working run after the sale has traded, the control is still in force."""
    listed = market(securities=THREE_BONDS, universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}))
    controls = frame(CONTROLS, {"security_id": B, "action": "must_sell", "reason": "downgrade"})

    lines = prepare(listed | portfolio(controls=controls)).lines.set_index("security_id")

    assert lines["buyable"].to_dict() == {A: True, B: False}
    assert not lines["must_sell"].any()


def test_cash_may_be_overdrawn_but_no_other_holding_may_be_short() -> None:
    overdrawn = frame(HOLDINGS, {"quantity": -50_000.0}, {"security_id": A})

    assert prepare(market() | portfolio(holdings=overdrawn)).cash == -50_000
    with pytest.raises(ValueError, match="negative quantities of securities that are not cash"):
        prepare(market() | portfolio(holdings=frame(HOLDINGS, {"security_id": A, "quantity": -1000.0})))


def test_a_strategy_cannot_block_a_mandatory_sale() -> None:
    frames = market() | portfolio(holdings=frame(HOLDINGS, {}, {"security_id": A}), controls=frame(CONTROLS, {"action": "must_sell", "reason": "downgrade"}))
    inputs = prepare(frames)

    restricted = inputs.restrict_sales(pd.Series(data=False, index=inputs.lines.index))

    assert restricted.lines["sellable"].tolist() == [True]


@pytest.mark.parametrize(
    ("holding", "error"), [({"security_id": "UNKNOWN"}, "holds securities with no analytics"), ({"sleeve_id": "treasury"}, "holds positions in sleeves it does not have")], ids=["security", "sleeve"]
)
def test_a_holding_the_market_or_the_portfolio_cannot_place_fails_the_portfolio(holding: dict[str, object], error: str) -> None:
    with pytest.raises(LookupError, match=error):
        prepare(market() | portfolio(holdings=frame(HOLDINGS, {}, holding)))


def test_an_eligible_security_without_analytics_is_left_out() -> None:
    frames = market(universe=frame(UNIVERSE, {"security_id": A}, {"security_id": B}))

    assert build_market(frames, AS_OF).universes["U1"]["security_id"].tolist() == [A]


def test_a_benchmark_constituent_without_analytics_fails_the_run() -> None:
    frames = market(benchmarks=frame(BENCHMARKS, {"security_id": A, "weight": 0.5}, {"security_id": B, "weight": 0.5}))

    with pytest.raises(ValueError, match="1 benchmark constituents have no analytics"):
        build_market(frames, AS_OF)


@pytest.mark.parametrize("dataset", MARKET, ids=lambda dataset: dataset.name)
def test_a_market_dataset_with_no_rows_fails_the_run(dataset: Dataset) -> None:
    with pytest.raises(ValueError, match=f"{dataset.name} returned no rows"):
        build_market(market(**{dataset.name: frame(dataset)}), AS_OF)


def test_a_security_twice_in_the_signals_fails_the_join() -> None:
    """Replay reads frames that the loader did not check, as from a snapshot written by hand, so the join must refuse
    a duplicate rather than multiply rows."""
    twice = pd.concat([frame(SIGNALS, {}), frame(SIGNALS, {})], ignore_index=True)

    with pytest.raises(pd.errors.MergeError):
        build_market(market(signals=twice), AS_OF)


def test_a_security_twice_on_a_list_fails_the_join_of_holdings_and_lists() -> None:
    """Replay reads frames that the loader did not check, so the join must refuse a duplicate rather than multiply lines."""
    twice = pd.concat([frame(UNIVERSE, {}), frame(UNIVERSE, {})], ignore_index=True)

    with pytest.raises(pd.errors.MergeError):
        prepare(market(universe=twice) | portfolio())


def test_portfolios_come_once_each_in_the_order_asked() -> None:
    frames = portfolio(portfolios=frame(PORTFOLIOS, {"portfolio_id": "P1"}, {"portfolio_id": "P2"}))

    assert [p.details.portfolio_id for p in build_portfolios(frames, ["P2", "P1", "P2"])] == ["P2", "P1"]


def test_an_unknown_portfolio_fails_the_run() -> None:
    with pytest.raises(LookupError, match="1 unknown portfolios"):
        build_portfolios(portfolio(), ["P1", "P9"])


def test_each_portfolio_gets_its_own_rows() -> None:
    frames = portfolio(portfolios=frame(PORTFOLIOS, {"portfolio_id": "P1"}, {"portfolio_id": "P2"}), holdings=frame(HOLDINGS, {}, {"security_id": A}))

    p1, p2 = build_portfolios(frames, ["P1", "P2"])

    assert p1.holdings["security_id"].tolist() == [USD, A]
    assert_frame_equal(p2.holdings, empty_frame(Holding))


def test_a_snapshot_gives_back_the_frames_the_run_loaded_exactly(tmp_path: Path) -> None:
    priced = frame(SECURITIES, {"price": 99.123456789, "accrued": 1.0416666667}, CASH)
    frames = market(securities=priced) | portfolio(holdings=frame(HOLDINGS, {"quantity": 1234567.89}))
    now = datetime(2026, 10, 16, 13, tzinfo=UTC)
    request = OptimizeRequest(strategy=STRATEGY, portfolios=["P1"], as_of_date=AS_OF)
    info = RunInfo(
        run_id="run-1",
        job="optimize",
        as_of_date=AS_OF,
        started_at=now,
        loaded_at=now,
        finished_at=now,
        code=Code(git_sha=None, image=None, dirty=None),
        labels={"purpose": "test"},
        workers=1,
        datasets={},
        request=request,
        replay=None,
    )

    save_run(Root.at(tmp_path), "run-1", meta=info, frames=frames, results=[])
    meta, saved = load_run(Root.at(tmp_path) / "run-1")

    assert RunInfo.model_validate(meta) == info
    assert saved.keys() == frames.keys()
    for name, loaded in frames.items():
        assert_frame_equal(saved[name], loaded)  # dtypes included: replay must prepare exactly what the run did


def test_a_replay_solves_as_the_run_did(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE)
    replayed = replay(ReplayRequest(snapshot=str(tmp_path / ran.run_id)), workers=1)

    assert ran.ok
    assert replayed.ok
    for live, again in zip(ran.results, replayed.results, strict=True):
        assert isinstance(live, Solved)
        assert isinstance(again, Solved)
        assert again.strategy == live.strategy
        assert not live.trades.empty
        assert_frame_equal(again.trades, live.trades)


def test_a_run_reads_its_portfolios_in_one_read_only_transaction_a_batch_with_the_holdings_asked_for(tmp_path: Path) -> None:
    source = sources()

    run(context(tmp_path, source), OptimizeRequest(strategy=STRATEGY, portfolios=["P1", "P2"], as_of_date=AS_OF, holdings="sod"))

    assert source.transactions == 1  # two portfolios, one batch
    assert [params["basis"] for query, params in source.fetches if query == HOLDINGS.query] == ["sod"]
    assert {query for query, _ in source.fetches} >= {d.query for d in (PORTFOLIOS, SLEEVES, HOLDINGS, CONTROLS, CASH_FLOWS)}
