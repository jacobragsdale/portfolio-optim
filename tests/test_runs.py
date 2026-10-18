"""What a run records about itself in `run.json`, and what each of its trades realizes."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from builders import JOBS, context, sources

from optim.data.loader import DatasetRead, Reads
from optim.datasets.portfolios import LOSS_SALES, LOTS, PORTFOLIOS
from optim.inputs import MARKET
from optim.jobs import Job, Replayed, ReplayRequest, RunInfo, replay, run
from optim.outputs.snapshots import Code

OPTIMIZE = JOBS[0]


def saved(path: Path) -> RunInfo:
    return RunInfo.model_validate_json((path / "run.json").read_bytes())


def test_a_run_records_when_it_ran_who_labelled_it_and_how_each_dataset_was_read(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE, labels={"purpose": "test"})

    info = saved(tmp_path / ran.run_id)

    assert info.started_at < info.loaded_at < info.finished_at
    assert (info.labels, info.workers) == ({"purpose": "test"}, 1)
    assert info.datasets is not None
    assert list(info.datasets) == sorted(dataset.name for dataset in [*MARKET, PORTFOLIOS, LOTS, LOSS_SALES])
    assert all(read.read == "live" and info.started_at < read.fetched_at < info.loaded_at for read in info.datasets.values())


def test_a_replay_records_the_run_it_replays_and_reads_no_dataset(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE)

    replayed = replay(ReplayRequest(snapshot=tmp_path / ran.run_id), workers=1, labels={"purpose": "research"})
    info = saved(tmp_path / replayed.run_id)

    assert info.replay == Replayed(of=ran.run_id, portfolios=None)
    assert info.datasets is None
    assert info.labels == {"purpose": "research"}


def test_a_dataset_loaded_in_batches_keeps_its_oldest_read() -> None:
    reads, early, late = Reads(), datetime(2026, 10, 16, 13, tzinfo=UTC), datetime(2026, 10, 16, 14, tzinfo=UTC)

    reads.add("lots", DatasetRead(read="live", fetched_at=late))
    reads.add("lots", DatasetRead(read="hit", fetched_at=early))
    reads.add("lots", DatasetRead(read="live", fetched_at=late))

    assert reads.by_dataset() == {"lots": DatasetRead(read="hit", fetched_at=early)}


def test_an_images_commit_is_recorded_rather_than_the_checkouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIM_GIT_SHA", "abc123")
    monkeypatch.setenv("OPTIM_IMAGE", "registry/portfolio-optim:abc123")

    assert Code.current() == Code(git_sha="abc123", image="registry/portfolio-optim:abc123", dirty=None)


@pytest.mark.parametrize("job", JOBS, ids=lambda job: job.job)
def test_each_sale_carries_its_gain_and_tax_and_they_sum_to_the_summary(tmp_path: Path, job: Job) -> None:
    ran = run(context(tmp_path, sources()), job)

    trades, summary = (pd.read_parquet(tmp_path / ran.run_id / f"{name}.parquet") for name in ("trades", "summary"))
    sells = trades["side"] == "sell"
    taxed = trades.groupby(["portfolio_id", "strategy"])["tax"].sum()

    assert trades.loc[sells, ["gain", "tax"]].notna().all(axis=None)
    assert trades.loc[~sells, ["gain", "tax"]].isna().all(axis=None)
    for row in summary.itertuples():
        assert row.tax == pytest.approx(taxed.get((row.portfolio_id, row.strategy), 0.0), abs=1e-9)


def test_a_sale_below_basis_realizes_a_loss_and_negative_tax(tmp_path: Path) -> None:
    """P1 holds bond B bought at 104, now at a clean 99.5: the solve harvests the loss."""
    ran = run(context(tmp_path, sources()), OPTIMIZE)

    trades = pd.read_parquet(tmp_path / ran.run_id / "trades.parquet")
    [sale] = trades[trades["side"] == "sell"].to_dict("records")

    assert (sale["portfolio_id"], sale["lot_id"]) == ("P1", "L1")
    assert sale["gain"] == pytest.approx(-0.045 * sale["par"])
    assert sale["tax"] == pytest.approx(0.25 * sale["gain"])  # held over a year: the long-term rate
