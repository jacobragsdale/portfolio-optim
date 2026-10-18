"""Test data: one valid row per dataset, frames built from partial rows, and a source serving them, so a test can run
any job end to end, snapshot included, with no database."""

from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from optim.context import Context
from optim.data.loader import Dataset, Loader
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.portfolios import LOSS_SALES, LOTS, PORTFOLIOS
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.inputs import MARKET
from optim.jobs.model_portfolio import ModelPortfolioRequest
from optim.jobs.optimize import OptimizeRequest
from optim.jobs.transition import TransitionRequest
from optim.sources import Columns, Param
from optim.strategy import Strategy

AS_OF = date(2026, 10, 16)
A, B, C = "AAAAAAAA1", "BBBBBBBB2", "CCCCCCCC3"
STRATEGY = Strategy(name="s")

DEFAULTS: dict[str, dict[str, object]] = {  # one valid row per dataset
    "securities": {
        "cusip": A,
        "issuer": "Issuer A",
        "sector": "Corporate",
        "rating": "AA",
        "tax_status": "taxable",
        "maturity": date(2031, 6, 1),
        "price": 100.0,
        "accrued": 0.5,
        "duration": 4.5,
        "cost_bps": 5.0,
        "min_piece": 1000,
        "increment": 1000,
        "min_remaining": 1000,
    },
    "universe": {"cusip": A, "offer_par": 1_000_000},
    "signals": {"cusip": A, "expected_return": 0.05},
    "benchmarks": {"benchmark_id": "B1", "cusip": A, "weight": 1.0},
    "portfolios": {"portfolio_id": "P1", "benchmark_id": "B1", "cash": 1_000_000.0, "federal_rate": 0.37, "state_rate": 0.05, "st_gain_rate": 0.42, "lt_gain_rate": 0.25},
    "lots": {"portfolio_id": "P1", "lot_id": "L1", "cusip": A, "par": 100_000, "cost_price": 101.0, "acquired": date(2024, 3, 1)},
    "loss_sales": {"portfolio_id": "P1", "cusip": A, "sold": date(2026, 10, 1)},
}


def columns(dataset: Dataset, rows: Iterable[Mapping[str, object]]) -> Columns:
    """`rows` as a source returns them; fields a row leaves out take the dataset's default."""
    full = [DEFAULTS[dataset.name] | dict(row) for row in rows]
    return {name: [row[name] for row in full] for name in dataset.row.model_fields}


def frame(dataset: Dataset, *rows: Mapping[str, object]) -> pd.DataFrame:
    return dataset.to_frame(columns(dataset, rows))


def market(**frames: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Bond A, offered with a signal and the whole of benchmark B1; `frames` replace datasets by name."""
    return {dataset.name: frame(dataset, {}) for dataset in MARKET} | frames


class Rows:
    """A source that answers each dataset's query with its rows, and a batch of portfolios with theirs alone."""

    def __init__(self, rows: Mapping[Dataset, Iterable[Mapping[str, object]]]) -> None:
        self.rows = {dataset.query: (dataset, [DEFAULTS[dataset.name] | dict(row) for row in dataset_rows]) for dataset, dataset_rows in rows.items()}
        self.queries: list[str] = []

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        self.queries.append(query)
        dataset, rows = self.rows[query]
        if isinstance(ids := params.get("portfolios"), tuple):
            rows = [row for row in rows if row["portfolio_id"] in ids]
        return columns(dataset, rows)


def sources() -> Rows:
    """Two bonds in benchmark B1 and a third on offer without a signal; P1 holds B at a loss, P2 sold A at one."""
    rows = {
        SECURITIES: [{"cusip": A}, {"cusip": B, "issuer": "Issuer B"}, {"cusip": C, "issuer": "Issuer C"}],
        UNIVERSE: [{"cusip": A}, {"cusip": B}, {"cusip": C}],
        SIGNALS: [{"cusip": A, "expected_return": 0.05}, {"cusip": B, "expected_return": 0.03}],
        BENCHMARKS: [{"cusip": A, "weight": 0.5}, {"cusip": B, "weight": 0.5}],
        PORTFOLIOS: [{"portfolio_id": "P1"}, {"portfolio_id": "P2"}],
        LOTS: [{"portfolio_id": "P1", "cusip": B, "cost_price": 104.0}],
        LOSS_SALES: [{"portfolio_id": "P2", "cusip": A}],
    }
    return Rows(rows)


def context(snapshot_dir: Path, source: Rows) -> Context:
    """Runs on `source`, each saved as `run-1`, with a clock the run and its loads share."""
    clock = Clock()
    return Context(Loader({"oracle": source, "mssql": source, "signals": source}, clock=clock), snapshot_dir, workers=1, new_run_id=lambda: "run-1", clock=clock)


JOBS = [
    OptimizeRequest(strategy=STRATEGY, portfolios=["P1", "P2"], as_of_date=AS_OF),
    TransitionRequest(strategy=STRATEGY, portfolio_id="P1", tax_budgets=[0, 10_000], as_of_date=AS_OF),
    ModelPortfolioRequest(strategy=STRATEGY, benchmark_id="B1", notional=1_000_000, as_of_date=AS_OF),
]


class Clock:
    """A clock a second later at each reading, from the business date's 9 am in New York."""

    def __init__(self) -> None:
        self._ticks: Iterator[int] = iter(range(10**6))

    def __call__(self) -> datetime:
        return datetime(2026, 10, 16, 13, tzinfo=UTC) + timedelta(seconds=next(self._ticks))
