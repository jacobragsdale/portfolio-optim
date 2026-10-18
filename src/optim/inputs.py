"""What a solve sees, and how datasets combine into it: a `Market` shared by every portfolio in a run, each
`Portfolio`, and the per-solve `SolverInputs` that rules edit. A new per-security dataset is merged in `load_market`; a new
per-portfolio one is loaded in `load_portfolios`."""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from optim.data.frames import empty_frame, to_models
from optim.data.loader import Loader, TaskGroup
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.portfolios import LOSS_SALES, LOTS, PORTFOLIOS, LossSale, Lot, PortfolioDetails
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.logs import ms_since

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Portfolio:
    details: PortfolioDetails
    lots: pd.DataFrame  # its tax lots (`Lot` columns)
    loss_sales: pd.DataFrame  # bonds it sold at a loss in the last 30 days (`LossSale` columns)


@dataclass(frozen=True)
class SolverInputs:
    """Everything one solve sees. Rules return edited copies; nothing is mutated in place."""

    as_of_date: date
    portfolio: PortfolioDetails
    lots: pd.DataFrame
    loss_sales: pd.DataFrame
    securities: pd.DataFrame  # `Security` columns + offer_par, expected_return, buyable; rules edit the last two
    target: pd.Series  # weight by cusip: the benchmark, or a model portfolio to move toward

    def restrict(self, allowed: pd.Series) -> SolverInputs:
        """Narrow what may be bought. Rows stay: a bond you may not buy can still be held, and sold."""
        return replace(self, securities=self.securities.assign(buyable=self.securities["buyable"] & allowed))


@dataclass(frozen=True)
class Market:
    """Data shared by every portfolio in a run: loaded once, shipped once to each worker process."""

    as_of_date: date
    securities: pd.DataFrame
    targets: Mapping[str, pd.Series]  # benchmark_id -> weight by cusip

    def inputs_for(self, portfolio: Portfolio) -> SolverInputs:
        details = portfolio.details
        if details.benchmark_id not in self.targets:
            msg = f"portfolio {details.portfolio_id}: benchmark {details.benchmark_id} is not loaded"
            raise LookupError(msg)
        # Every portfolio in a process reads the same frames. Shallow copies cost nothing under copy-on-write, and keep a
        # rule that edits a frame in place from changing what the next portfolio sees.
        target = self.targets[details.benchmark_id]
        return SolverInputs(self.as_of_date, details, portfolio.lots.copy(deep=False), portfolio.loss_sales.copy(deep=False), self.securities.copy(deep=False), target.copy(deep=False))


def load_market(loader: Loader, as_of_date: date) -> Market:
    start = time.perf_counter()
    frames = loader.load_all(SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS, as_of_date=as_of_date)
    analytics, universe, benchmarks = frames["securities"], frames["universe"], frames["benchmarks"]
    if empty := [name for name, frame in frames.items() if frame.empty]:  # an empty signals reply would leave nothing buyable, and still trade
        msg = f"{', '.join(empty)} returned no rows for {as_of_date}: is it a business day, and has every feed landed? Solving without them would trade on part of the market"
        raise ValueError(msg)

    # get_indexer, not isin: pandas 3 builds a pyarrow scalar per value for isin on strings, ~300ms per 100k cusips
    if (unpriced := pd.Index(analytics["cusip"]).get_indexer(pd.Index(universe["cusip"])) < 0).any():
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
    if (orphans := pd.Index(securities["cusip"]).get_indexer(pd.Index(benchmarks["cusip"])) < 0).any():
        msg = f"{orphans.sum()} benchmark constituents have no analytics, e.g. {benchmarks.loc[orphans].head(3).to_dict('records')}"
        raise ValueError(msg)
    targets = {str(benchmark_id): group.set_index("cusip")["weight"] for benchmark_id, group in benchmarks.groupby("benchmark_id")}
    log.info("loaded market", extra={"as_of_date": as_of_date, "securities": len(securities), "buyable": int(securities["buyable"].sum()), "benchmarks": len(targets), "ms": ms_since(start)})
    return Market(as_of_date, securities, targets)


def load_inputs(loader: Loader, portfolio_ids: Sequence[str], as_of_date: date) -> tuple[Market, list[Portfolio]]:
    """The market and the portfolios, loaded at the same time."""
    with TaskGroup() as group:
        market = group.submit(load_market, loader, as_of_date)
        portfolios = group.submit(load_portfolios, loader, portfolio_ids, as_of_date)
    return market.result(), portfolios.result()


def load_portfolios(loader: Loader, portfolio_ids: Sequence[str], as_of_date: date) -> list[Portfolio]:
    """Load portfolios, lots and the loss sales of the 30 days to `as_of_date`, all three at once and in batches, in the
    order given."""
    start = time.perf_counter()
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    with TaskGroup() as group:
        portfolios = group.submit(loader.load_batched, PORTFOLIOS, "portfolios", portfolio_ids)
        lots = group.submit(loader.load_batched, LOTS, "portfolios", portfolio_ids)
        loss_sales = group.submit(loader.load_batched, LOSS_SALES, "portfolios", portfolio_ids, as_of_date=as_of_date)
    details = {d.portfolio_id: d for d in to_models(PortfolioDetails, portfolios.result())}
    if missing := [i for i in portfolio_ids if i not in details]:
        msg = f"{len(missing)} unknown portfolios, e.g. {missing[:5]}"
        raise LookupError(msg)
    log.info("loaded portfolios", extra={"portfolios": len(portfolio_ids), "lots": len(lots.result()), "loss_sales": len(loss_sales.result()), "ms": ms_since(start)})
    lots_of, sales_of = by_portfolio(lots.result()), by_portfolio(loss_sales.result())
    no_lots, no_sales = empty_frame(Lot), empty_frame(LossSale)
    return [Portfolio(details[i], lots_of.get(i, no_lots), sales_of.get(i, no_sales)) for i in portfolio_ids]


def by_portfolio(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """A per-portfolio frame, such as lots, split by `portfolio_id` for `Portfolio`."""
    return {str(portfolio_id): group.reset_index(drop=True) for portfolio_id, group in frame.groupby("portfolio_id")}
