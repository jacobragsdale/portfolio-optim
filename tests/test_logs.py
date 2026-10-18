"""A run's id is on every record the run logs, solver workers' records included."""

import json
from datetime import date

import pandas as pd
import pytest

from optim import logs
from optim.data.frames import empty_frame
from optim.datasets.portfolios import PortfolioDetails
from optim.engine import SolveTask, solve_all
from optim.inputs import PER_PORTFOLIO, Market, Portfolio
from optim.strategy import Strategy


def test_workers_log_the_bound_run_id(capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIM_LOG_FORMAT", "json")
    monkeypatch.setattr(logs, "_bound", {})
    logs.setup()
    logs.bind(run_id="run-1")
    details = [PortfolioDetails(portfolio_id=f"A{i}", currency="USD", target_value=None) for i in (1, 2)]
    tasks = [SolveTask(Portfolio(d, *(empty_frame(dataset.row) for dataset in PER_PORTFOLIO)), Strategy(name="s")) for d in details]

    solve_all(Market(date(2026, 10, 16), pd.DataFrame(), pd.Index([]), {}, {}), tasks, workers=2)  # each solve fails, and logs it, in a worker

    records = [json.loads(line) for line in capfd.readouterr().err.splitlines()]
    assert {r["portfolio_id"] for r in records if r["message"] == "solve failed"} == {"A1", "A2"}
    assert all(r["run_id"] == "run-1" for r in records)
