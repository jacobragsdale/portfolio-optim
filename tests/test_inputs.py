"""How the datasets a run loads become what its solves see: the same prep live and on replay."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from builders import AS_OF, OPTIMIZE, STRATEGY, A, B, C, context, frame, market, sources
from pandas.testing import assert_frame_equal

from optim.data.frames import empty_frame
from optim.data.loader import Dataset
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.portfolios import LOTS, PORTFOLIOS, Lot
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.inputs import MARKET, build_market, build_portfolios
from optim.jobs import ReplayRequest, RunInfo, replay, run
from optim.jobs.optimize import OptimizeRequest
from optim.optimizer.results import Solved
from optim.outputs.snapshots import Code, load_run, save_run


def test_builder_rows_pass_their_dataset_schemas() -> None:
    for dataset in [*MARKET, PORTFOLIOS, LOTS]:
        assert len(frame(dataset, {})) == 1


def test_a_bond_is_buyable_when_offered() -> None:
    frames = market(
        securities=frame(SECURITIES, {"cusip": A}, {"cusip": B}, {"cusip": C}),
        universe=frame(UNIVERSE, {"cusip": A}, {"cusip": B}),
        signals=frame(SIGNALS, {"cusip": A, "expected_return": 0.05}, {"cusip": C, "expected_return": 0.04}),
    )

    securities = build_market(frames, AS_OF).securities

    assert securities[["cusip", "buyable", "expected_return"]].to_dict("records") == [
        {"cusip": A, "buyable": True, "expected_return": 0.05},
        {"cusip": B, "buyable": True, "expected_return": 0.0},  # offered, without a signal
        {"cusip": C, "buyable": False, "expected_return": 0.04},  # a view, but not offered
    ]


def test_an_offering_without_analytics_is_left_out() -> None:
    frames = market(universe=frame(UNIVERSE, {"cusip": A}, {"cusip": B}))

    assert build_market(frames, AS_OF).securities["cusip"].tolist() == [A]


def test_a_benchmark_bond_without_analytics_fails_the_run() -> None:
    frames = market(benchmarks=frame(BENCHMARKS, {"cusip": A, "weight": 0.5}, {"cusip": B, "weight": 0.5}))

    with pytest.raises(ValueError, match="1 benchmark constituents have no analytics"):
        build_market(frames, AS_OF)


@pytest.mark.parametrize("dataset", MARKET, ids=lambda dataset: dataset.name)
def test_a_market_dataset_with_no_rows_fails_the_run(dataset: Dataset) -> None:
    with pytest.raises(ValueError, match=f"{dataset.name} returned no rows"):
        build_market(market(**{dataset.name: frame(dataset)}), AS_OF)


@pytest.mark.parametrize("dataset", [UNIVERSE, SIGNALS], ids=lambda dataset: dataset.name)
def test_a_bond_twice_in_a_joined_dataset_fails_the_join(dataset: Dataset) -> None:
    """Replay reads frames that the loader did not check, as from a snapshot written by hand, so the join must refuse
    a duplicate rather than multiply rows."""
    twice = pd.concat([frame(dataset, {}), frame(dataset, {})], ignore_index=True)

    with pytest.raises(pd.errors.MergeError):
        build_market(market(**{dataset.name: twice}), AS_OF)


def test_portfolios_come_once_each_in_the_order_asked() -> None:
    frames = {"portfolios": frame(PORTFOLIOS, {"portfolio_id": "P1"}, {"portfolio_id": "P2"}), "lots": frame(LOTS)}

    assert [p.details.portfolio_id for p in build_portfolios(frames, ["P2", "P1", "P2"])] == ["P2", "P1"]


def test_an_unknown_portfolio_fails_the_run() -> None:
    frames = {"portfolios": frame(PORTFOLIOS, {"portfolio_id": "P1"}), "lots": frame(LOTS)}

    with pytest.raises(LookupError, match="1 unknown portfolios"):
        build_portfolios(frames, ["P1", "P9"])


def test_each_portfolio_gets_its_own_lots() -> None:
    frames = {"portfolios": frame(PORTFOLIOS, {"portfolio_id": "P1"}, {"portfolio_id": "P2"}), "lots": frame(LOTS, {"portfolio_id": "P1", "lot_id": "L1"}, {"portfolio_id": "P1", "lot_id": "L2"})}

    p1, p2 = build_portfolios(frames, ["P1", "P2"])

    assert p1.lots["lot_id"].tolist() == ["L1", "L2"]
    assert_frame_equal(p2.lots, empty_frame(Lot))


def test_a_snapshot_gives_back_the_frames_the_run_loaded(tmp_path: Path) -> None:
    frames = market() | {"portfolios": frame(PORTFOLIOS, {}), "lots": frame(LOTS, {})}
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

    save_run(tmp_path, "run-1", meta=info, frames=frames, results=[])
    meta, saved = load_run(tmp_path / "run-1")

    assert RunInfo.model_validate(meta) == info
    assert saved.keys() == frames.keys()
    for name, loaded in frames.items():
        assert_frame_equal(saved[name], loaded)  # dtypes included: replay must prepare exactly what the run did


def test_a_replay_solves_as_the_run_did(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE)
    replayed = replay(ReplayRequest(snapshot=tmp_path / ran.run_id), workers=1)

    assert ran.ok
    assert replayed.ok
    for live, again in zip(ran.results, replayed.results, strict=True):
        assert isinstance(live, Solved)
        assert isinstance(again, Solved)
        assert again.strategy == live.strategy
        assert not live.trades.empty
        assert_frame_equal(again.trades, live.trades)
