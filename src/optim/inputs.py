"""What the optimizer reads: every dataset we load, and how they combine into a `Market` (shared by every portfolio in
a run) and the per-portfolio `Inputs` that rules and the solver see.

To add a dataset: write its `Row`, add a query in `queries/`, declare a `Dataset` below, and merge it in `load_market`
(per-security data) or `load_portfolios` (per-account data).
"""

import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Literal

import pandas as pd
from pydantic import Field, StringConstraints

from optim.data import Dataset, Date, Loader, Row, empty_frame, to_models

log = logging.getLogger(__name__)

Cusip = Annotated[str, StringConstraints(pattern=r"^[0-9A-Z*@#]{8}[0-9]$")]
State = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
Rate = Annotated[float, Field(ge=0, lt=1)]
Price = Annotated[float, Field(gt=0)]  # per 100 par
Par = Annotated[int, Field(gt=0)]  # face amount in whole dollars: orders are exact, the solver works in float64
Weight = Annotated[float, Field(ge=0, le=1)]
Rating = Literal[
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D",
]  # fmt: skip


class Account(Row):
    account_id: str
    benchmark_id: str
    cash: float
    state: State | None  # tax residence; None when no state income tax applies
    federal_rate: Rate  # marginal rate on ordinary income
    state_rate: Rate
    st_gain_rate: Rate  # short-term capital gains, federal + state
    lt_gain_rate: Rate  # long-term capital gains, federal + state


class Lot(Row):
    account_id: str
    lot_id: str
    cusip: Cusip
    par: Par
    cost_price: Price  # adjusted tax basis
    acquired: Date


class Offering(Row):
    cusip: Cusip
    offer_par: Par  # par we can buy today


class Security(Row):
    cusip: Cusip
    issuer: str
    sector: str
    state: State | None  # munis only
    rating: Rating
    tax_status: Literal["taxable", "muni"]
    maturity: Date
    price: Price  # dirty
    duration: Annotated[float, Field(ge=0)]  # modified, years
    cost_bps: Annotated[float, Field(ge=0)]  # estimated one-way trading cost
    min_piece: Par
    increment: Par


class Signal(Row):
    cusip: Cusip
    expected_return: float  # annual, pre-tax, decimal


class BenchmarkWeight(Row):
    benchmark_id: str
    cusip: Cusip
    weight: Annotated[float, Field(gt=0, le=1)]


def _weights_sum_to_one(frame: pd.DataFrame) -> None:
    totals = frame.groupby("benchmark_id")["weight"].sum()
    if not (bad := totals[(totals - 1).abs() > 1e-6]).empty:
        msg = f"benchmark weights must sum to 1: {bad.head().to_dict()}"
        raise ValueError(msg)


ACCOUNTS = Dataset("accounts", Account, source="mssql", query="accounts.sql", key=("account_id",))
LOTS = Dataset("lots", Lot, source="mssql", query="lots.sql", key=("account_id", "lot_id"))
UNIVERSE = Dataset("universe", Offering, source="oracle", query="universe.sql", key=("cusip",), ttl=timedelta(minutes=15))
SECURITIES = Dataset("securities", Security, source="oracle", query="securities.sql", key=("cusip",), ttl=timedelta(minutes=15))
SIGNALS = Dataset("signals", Signal, source="signals", query="/v1/expected-returns", key=("cusip",), ttl=timedelta(minutes=5))
BENCHMARKS = Dataset("benchmarks", BenchmarkWeight, source="oracle", query="benchmarks.sql", key=("benchmark_id", "cusip"), ttl=timedelta(hours=12), check=_weights_sum_to_one)


@dataclass(frozen=True)
class Portfolio:
    account: Account
    lots: pd.DataFrame  # this account's tax lots (`Lot` columns)


@dataclass(frozen=True)
class Inputs:
    """Everything one solve sees. Rules return an edited copy; nothing is mutated in place."""

    as_of: date
    account: Account
    lots: pd.DataFrame
    securities: pd.DataFrame  # `Security` columns + offer_par, expected_return, buyable; rules edit the last two
    target: pd.Series  # weight by cusip: the benchmark, or a model portfolio to move toward


@dataclass(frozen=True)
class Market:
    """Data shared by every portfolio in a run: loaded once, shipped once to each worker process."""

    as_of: date
    securities: pd.DataFrame
    targets: Mapping[str, pd.Series]  # benchmark_id -> weight by cusip

    def inputs_for(self, portfolio: Portfolio) -> Inputs:
        account = portfolio.account
        if account.benchmark_id not in self.targets:
            msg = f"account {account.account_id}: benchmark {account.benchmark_id} is not loaded"
            raise LookupError(msg)
        return Inputs(self.as_of, account, portfolio.lots, self.securities, self.targets[account.benchmark_id])


def load_market(loader: Loader, as_of: date) -> Market:
    with ThreadPoolExecutor() as pool:  # independent sources: fetch concurrently
        analytics, universe, signals, benchmarks = pool.map(lambda dataset: loader.load(dataset, as_of=as_of), [SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS])

    if (unpriced := ~universe["cusip"].isin(analytics["cusip"])).any():
        log.warning("%d offerings have no analytics and cannot be bought", unpriced.sum(), extra={"cusips": universe.loc[unpriced, "cusip"].head(20).tolist()})
    securities = (
        analytics.merge(universe, on="cusip", how="left", validate="one_to_one")
        .merge(signals, on="cusip", how="left", validate="one_to_one")
        .assign(
            buyable=lambda s: s["offer_par"].notna() & s["expected_return"].notna(),  # offered, and we have a view
            expected_return=lambda s: s["expected_return"].fillna(0.0),  # no view on a held bond: no excess return
        )
    )
    # A target we cannot price would silently shift benchmark duration and tracking, so fail loudly.
    if (orphans := ~benchmarks["cusip"].isin(securities["cusip"])).any():
        msg = f"{orphans.sum()} benchmark constituents have no analytics, e.g. {benchmarks.loc[orphans].head(3).to_dict('records')}"
        raise ValueError(msg)
    targets = {str(benchmark_id): group.set_index("cusip")["weight"] for benchmark_id, group in benchmarks.groupby("benchmark_id")}
    return Market(as_of, securities, targets)


def load_portfolios(loader: Loader, account_ids: Sequence[str]) -> list[Portfolio]:
    """Load accounts and lots in batches, in the order given."""
    account_ids = list(dict.fromkeys(account_ids))
    accounts = {a.account_id: a for a in to_models(Account, loader.load_batched(ACCOUNTS, "accounts", account_ids))}
    if missing := [i for i in account_ids if i not in accounts]:
        msg = f"{len(missing)} unknown accounts, e.g. {missing[:5]}"
        raise LookupError(msg)
    lots = loader.load_batched(LOTS, "accounts", account_ids)
    by_account = {str(account_id): group.reset_index(drop=True) for account_id, group in lots.groupby("account_id")}
    no_lots = empty_frame(Lot)
    return [Portfolio(accounts[i], by_account.get(i, no_lots)) for i in account_ids]
