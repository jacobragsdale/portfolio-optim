"""Test data: one valid row per dataset, frames built from partial rows, and a source serving them, so a test can run
any job end to end, snapshot included, with no database."""

from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from optim.context import Context
from optim.data.loader import Dataset, Loader
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, PORTFOLIOS, SLEEVES
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.inputs import MARKET, PER_PORTFOLIO, SolverInputs, build_market, build_portfolios
from optim.jobs.optimize import OptimizeRequest
from optim.sources import Columns, Param
from optim.strategy import Strategy

AS_OF = date(2026, 10, 16)
A, B, C = "AAAAAAAA1", "BBBBBBBB2", "CCCCCCCC3"
USD = "USD"  # the cash security
STRATEGY = Strategy(name="s")

# TODO(port): one valid row for each dataset the port declares; tests build every frame from these.
DEFAULTS: dict[str, dict[str, object]] = {  # one valid row per dataset
    "securities": {
        "security_id": A,
        "cusip": A,
        "isin": None,
        "asset_class": "bond",
        "currency": "USD",
        "issuer": "Issuer A",
        "sector": "Corporate",
        "rating": "AA",
        "maturity": date(2030, 10, 15),
        "duration": 4.0,
        "price": Decimal(100),
        "accrued": Decimal(0),
        "price_per": 100,
        "cost_bps": 5.0,
        "min_piece": 1000,
        "increment": 1000,
        "min_remaining": 1000,
    },
    "universe": {"universe_id": "U1", "security_id": A, "available": None},
    "signals": {"security_id": A, "expected_return": 0.05},
    "benchmarks": {"benchmark_id": "B1", "security_id": A, "weight": 1.0},
    "portfolios": {"portfolio_id": "P1", "currency": "USD", "target_value": None},
    "sleeves": {"portfolio_id": "P1", "sleeve_id": "core", "universe_id": "U1", "benchmark_id": "B1", "target_weight": 1.0},
    "holdings": {"portfolio_id": "P1", "sleeve_id": "core", "security_id": USD, "quantity": Decimal(1_000_000)},
    "controls": {"portfolio_id": "P1", "control_id": "R1", "security_id": A, "action": "no_buy", "reason": "wash_sale"},
    "cash_flows": {"portfolio_id": "P1", "flow_id": "F1", "amount": Decimal(0)},
}
# the cash security: priced at 1 per unit, with no denominations or analytics
CASH: dict[str, object] = {
    "security_id": USD,
    "cusip": None,
    "asset_class": "cash",
    "issuer": None,
    "sector": None,
    "rating": None,
    "maturity": None,
    "duration": None,
    "price": Decimal(1),
    "price_per": 1,
    "cost_bps": 0.0,
    "min_piece": None,
    "increment": None,
    "min_remaining": None,
}


def columns(dataset: Dataset, rows: Iterable[Mapping[str, object]]) -> Columns:
    """`rows` as a source returns them; fields a row leaves out take the dataset's default."""
    full = [DEFAULTS[dataset.name] | dict(row) for row in rows]
    return {name: [row[name] for row in full] for name in dataset.row.model_fields}


def frame(dataset: Dataset, *rows: Mapping[str, object]) -> pd.DataFrame:
    return dataset.to_frame(columns(dataset, rows))


def market(**frames: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Bond A, on list U1 with a signal and the whole of benchmark B1, and cash; `frames` replace datasets by name."""
    return {dataset.name: frame(dataset, {}) for dataset in MARKET} | {"securities": frame(SECURITIES, {}, CASH)} | frames


def portfolio(**frames: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """P1: one sleeve, `core`, buying from U1 against B1, holding $1mm cash, with no controls or cash flows; `frames`
    replace datasets by name."""
    return {"portfolios": frame(PORTFOLIOS, {}), "sleeves": frame(SLEEVES, {}), "holdings": frame(HOLDINGS, {}), "controls": frame(CONTROLS), "cash_flows": frame(CASH_FLOWS)} | frames


def prepare(frames: Mapping[str, pd.DataFrame], portfolio_id: str = "P1") -> SolverInputs:
    """What P1's solve sees, before any rule, from a run's frames."""
    [one] = build_portfolios(frames, [portfolio_id])
    return build_market(frames, AS_OF).inputs_for(one)


class Rows:
    """A source that answers each dataset's query with its rows, and a batch of portfolios with theirs alone. It records
    each fetch's params, and how many read-only transactions were opened."""

    def __init__(self, rows: Mapping[Dataset, Iterable[Mapping[str, object]]]) -> None:
        self.rows = {dataset.query: (dataset, [DEFAULTS[dataset.name] | dict(row) for row in dataset_rows]) for dataset, dataset_rows in rows.items()}
        self.fetches: list[tuple[str, Mapping[str, Param]]] = []
        self.transactions = 0

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        self.fetches.append((query, params))
        dataset, rows = self.rows[query]
        if isinstance(ids := params.get("portfolios"), tuple):
            rows = [row for row in rows if row["portfolio_id"] in ids]
        return columns(dataset, rows)

    @contextmanager
    def read_only(self) -> Generator[Rows]:
        self.transactions += 1
        yield self


def sources() -> Rows:
    """Two bonds in benchmark B1 and a third on list U1 without a signal, and cash. P1 holds bond B and cash, P2 only cash."""
    rows = {
        SECURITIES: [{"security_id": A}, {"security_id": B, "cusip": B, "issuer": "Issuer B"}, {"security_id": C, "cusip": C, "issuer": "Issuer C"}, CASH],
        UNIVERSE: [{"security_id": A}, {"security_id": B}, {"security_id": C}],
        SIGNALS: [{"security_id": A, "expected_return": 0.05}, {"security_id": B, "expected_return": 0.03}],
        BENCHMARKS: [{"security_id": A, "weight": 0.5}, {"security_id": B, "weight": 0.5}],
        PORTFOLIOS: [{"portfolio_id": "P1"}, {"portfolio_id": "P2"}],
        SLEEVES: [{"portfolio_id": "P1"}, {"portfolio_id": "P2"}],
        HOLDINGS: [{"portfolio_id": "P1", "security_id": B, "quantity": Decimal(100_000)}, {"portfolio_id": "P1"}, {"portfolio_id": "P2"}],
        CONTROLS: [],
        CASH_FLOWS: [],
    }
    return Rows(rows)


def context(snapshot_dir: Path, source: Rows) -> Context:
    """Runs on `source`, each saved as `run-1`, with a clock the run and its loads share."""
    clock = Clock()
    return Context(Loader({"oracle": source, "mssql": source, "signals": source}, clock=clock), snapshot_dir, workers=1, new_run_id=lambda: "run-1", clock=clock)


OPTIMIZE = OptimizeRequest(strategy=STRATEGY, portfolios=["P1", "P2"], as_of_date=AS_OF)
ALL_DATASETS = (*MARKET, PORTFOLIOS, *PER_PORTFOLIO)


class Clock:
    """A clock a second later at each reading, from the business date's 9 am in New York."""

    def __init__(self) -> None:
        self._ticks: Iterator[int] = iter(range(10**6))

    def __call__(self) -> datetime:
        return datetime(2026, 10, 16, 13, tzinfo=UTC) + timedelta(seconds=next(self._ticks))
