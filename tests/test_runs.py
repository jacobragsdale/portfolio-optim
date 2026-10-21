"""What a run records about itself in `run.json`, how it is named, the jobs that reach the engine, and how its results
reconcile."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from builders import ALL_DATASETS, AS_OF, OPTIMIZE, STRATEGY, USD, A, context, sources
from pydantic import ValidationError

from optim.cli import main
from optim.data.loader import DatasetRead, Reads
from optim.datasets.controls import Control
from optim.datasets.portfolios import Holding, PortfolioDetails, Sleeve
from optim.jobs.hypothetical import HypotheticalPortfolio, HypotheticalRequest
from optim.jobs.run import Replayed, ReplayRequest, RunInfo, replay, run
from optim.outputs.snapshots import Code


def saved(path: Path) -> RunInfo:
    return RunInfo.model_validate_json((path / "run.json").read_bytes())


def test_a_run_records_when_it_ran_who_labelled_it_and_how_each_dataset_was_read(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE, labels={"purpose": "test"})

    info = saved(tmp_path / ran.run_id)

    assert info.started_at < info.loaded_at < info.finished_at
    assert (info.labels, info.workers) == ({"purpose": "test"}, 1)
    assert info.datasets is not None
    assert list(info.datasets) == sorted(dataset.name for dataset in ALL_DATASETS)
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

    reads.add("holdings", DatasetRead(read="live", fetched_at=late))
    reads.add("holdings", DatasetRead(read="hit", fetched_at=early))
    reads.add("holdings", DatasetRead(read="live", fetched_at=late))

    assert reads.by_dataset() == {"holdings": DatasetRead(read="hit", fetched_at=early)}


def test_an_images_commit_is_recorded_rather_than_the_checkouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIM_GIT_SHA", "abc123")
    monkeypatch.setenv("OPTIM_IMAGE", "registry/portfolio-optim:abc123")

    assert Code.current() == Code(git_sha="abc123", image="registry/portfolio-optim:abc123", dirty=None)


def test_an_id_issued_outside_names_the_run_once(tmp_path: Path) -> None:
    source = sources()
    ran = run(context(tmp_path, source), OPTIMIZE, run_id="4711")
    fetched = len(source.fetches)

    with pytest.raises(FileExistsError, match="run 4711 already has a snapshot"):
        run(context(tmp_path, source), OPTIMIZE, run_id="4711")
    with pytest.raises(ValidationError):
        run(context(tmp_path, source), OPTIMIZE, run_id="../4712")

    assert ran.run_id == saved(tmp_path / "4711").run_id == "4711"
    assert len(source.fetches) == fetched  # refused before loading anything


def test_a_hypothetical_portfolio_solves_from_the_request_alone_and_replays(tmp_path: Path) -> None:
    prospect = HypotheticalPortfolio(
        details=PortfolioDetails(portfolio_id="H1", currency="USD", target_value=None),
        sleeves=[Sleeve(portfolio_id="H1", sleeve_id="core", universe_id="U1", benchmark_id="B1", target_weight=1.0)],
        holdings=[Holding(portfolio_id="H1", sleeve_id="core", security_id=USD, quantity=Decimal(500_000))],
        controls=[Control(portfolio_id="H1", control_id="R1", security_id=A, action="no_buy", reason="wash_sale")],
    )
    source = sources()

    ran = run(context(tmp_path, source), HypotheticalRequest(strategy=STRATEGY, portfolios=[prospect], as_of_date=AS_OF))
    replayed = replay(ReplayRequest(snapshot=tmp_path / ran.run_id), workers=1)

    assert ran.ok
    assert replayed.ok
    trades = pd.read_parquet(tmp_path / ran.run_id / "trades.parquet")
    assert set(trades["portfolio_id"]) == {"H1"}
    assert A not in trades["security_id"].tolist()  # its wash-sale control held
    assert source.transactions == 0  # no portfolio was read


def test_a_portfolio_with_rows_of_another_is_refused() -> None:
    with pytest.raises(ValidationError, match="has rows of other portfolios"):
        HypotheticalPortfolio(
            details=PortfolioDetails(portfolio_id="H1", currency="USD", target_value=None),
            sleeves=[Sleeve(portfolio_id="H2", sleeve_id="core", universe_id="U1", benchmark_id="B1", target_weight=1.0)],
        )


def test_cash_after_trading_reconciles_with_the_trades(tmp_path: Path) -> None:
    ran = run(context(tmp_path, sources()), OPTIMIZE)

    trades, summary = (pd.read_parquet(tmp_path / ran.run_id / f"{name}.parquet") for name in ("trades", "summary"))
    signed = trades["value"].where(trades["side"] == "sell", -trades["value"]).groupby(trades["portfolio_id"]).sum()

    assert set(trades["quantity"].map(type)) == {Decimal}
    for row in summary.to_dict("records"):
        assert row["cash"] == pytest.approx(1_000_000 + row["cash_flow"] + signed[row["portfolio_id"]] - row["cost"], abs=1e-6)


@pytest.mark.parametrize(("strategy", "code"), [("track-benchmark", 0), ("two-sleeves", 1)])
def test_a_replay_exits_zero_only_when_every_portfolio_is_accepted(tmp_path: Path, strategy: str, code: int) -> None:
    """The placeholder portfolios have one sleeve, `core`; `two-sleeves` names others, so neither solves."""
    ran = run(context(tmp_path, sources()), OPTIMIZE)
    path = Path(__file__).parent.parent / "strategies" / f"{strategy}.json"

    assert main(["replay", str(tmp_path / ran.run_id), "--strategy", str(path)]) == code
