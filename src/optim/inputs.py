"""What a solve sees, and how datasets combine into it: a `Market` shared by every portfolio in a run, each account's
`Portfolio`, and the per-solve `Inputs` that rules edit. A new per-security dataset is merged in `load_market`; a new
per-account one is loaded in `load_portfolios`."""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from optim.data.frames import empty_frame, to_models
from optim.data.loader import Loader
from optim.datasets.accounts import ACCOUNTS, LOTS, Account, Lot
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.logs import ms_since

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Portfolio:
    account: Account
    lots: pd.DataFrame  # this account's tax lots (`Lot` columns)


@dataclass(frozen=True)
class Inputs:
    """Everything one solve sees. Rules return edited copies; nothing is mutated in place."""

    as_of: date
    account: Account
    lots: pd.DataFrame
    securities: pd.DataFrame  # `Security` columns + offer_par, expected_return, buyable; rules edit the last two
    target: pd.Series  # weight by cusip: the benchmark, or a model portfolio to move toward

    def restrict(self, allowed: pd.Series) -> Inputs:
        """Narrow what may be bought. Rows stay: a bond you may not buy can still be held, and sold."""
        return replace(self, securities=self.securities.assign(buyable=self.securities["buyable"] & allowed))


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
    start = time.perf_counter()
    frames = loader.load_all(SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS, as_of=as_of)
    analytics, universe, benchmarks = frames["securities"], frames["universe"], frames["benchmarks"]

    if (unpriced := ~universe["cusip"].isin(analytics["cusip"])).any():
        log.warning("%d offerings have no analytics and cannot be bought", unpriced.sum(), extra={"cusips": universe.loc[unpriced, "cusip"].head(20).tolist()})
    securities = (
        analytics.merge(universe, on="cusip", how="left", validate="one_to_one")
        .merge(frames["signals"], on="cusip", how="left", validate="one_to_one")
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
    log.info("loaded market", extra={"as_of": as_of, "securities": len(securities), "buyable": int(securities["buyable"].sum()), "benchmarks": len(targets), "ms": ms_since(start)})
    return Market(as_of, securities, targets)


def load_portfolios(loader: Loader, account_ids: Sequence[str]) -> list[Portfolio]:
    """Load accounts and lots in batches, in the order given."""
    start = time.perf_counter()
    account_ids = list(dict.fromkeys(account_ids))
    accounts = {a.account_id: a for a in to_models(Account, loader.load_batched(ACCOUNTS, "accounts", account_ids))}
    if missing := [i for i in account_ids if i not in accounts]:
        msg = f"{len(missing)} unknown accounts, e.g. {missing[:5]}"
        raise LookupError(msg)
    lots = loader.load_batched(LOTS, "accounts", account_ids)
    by_account = {str(account_id): group.reset_index(drop=True) for account_id, group in lots.groupby("account_id")}
    no_lots = empty_frame(Lot)
    log.info("loaded portfolios", extra={"accounts": len(account_ids), "lots": len(lots), "ms": ms_since(start)})
    return [Portfolio(accounts[i], by_account.get(i, no_lots)) for i in account_ids]
