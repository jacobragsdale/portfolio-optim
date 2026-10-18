"""What a solve sees, and how it is prepared from the datasets a run loads.

`load_frames` is the only I/O here: it loads every dataset a run reads, one validated frame each, and the snapshot saves
those frames as they are. The rest is prep, pure and the same for every strategy: `build_market` joins the per-security
datasets into the `Market` every portfolio shares, `build_portfolios` splits the per-portfolio ones into `Portfolio`s,
and `Market.inputs_for` pairs one portfolio with the market as the `SolverInputs` that the strategy's rules edit. Replay
runs the same prep on a snapshot's frames. A new dataset is loaded in `load_frames`, then merged in `build_market` or
split in `build_portfolios`."""

import logging
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
from optim.logs import timed

log = logging.getLogger(__name__)

type Frames = Mapping[str, pd.DataFrame]  # a dataset's name -> its frame, as loaded

MARKET = (SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS)  # loaded once a run, for its business date


@dataclass(frozen=True)
class Portfolio:
    details: PortfolioDetails
    lots: pd.DataFrame  # its tax lots (`Lot` columns)
    loss_sales: pd.DataFrame  # bonds it sold at a loss in the last 30 days (`LossSale` columns)


@dataclass(frozen=True)
class SolverInputs:
    """Everything one solve sees. Rules return edited copies; nothing is mutated in place."""

    as_of_date: date
    portfolio: Portfolio  # its details, lots and loss sales
    securities: pd.DataFrame  # see `build_market`; rules edit `buyable` and `expected_return`
    target: pd.Series  # weight by cusip: the benchmark, or a model portfolio to move toward

    def restrict(self, allowed: pd.Series) -> SolverInputs:
        """Narrow what may be bought. Rows stay: a bond you may not buy can still be held, and sold."""
        return replace(self, securities=self.securities.assign(buyable=self.securities["buyable"] & allowed))


@dataclass(frozen=True)
class Market:
    """Data shared by every portfolio in a run: built once, shipped once to each worker process."""

    as_of_date: date
    securities: pd.DataFrame
    targets: Mapping[str, pd.Series]  # benchmark_id -> weight by cusip

    def inputs_for(self, portfolio: Portfolio) -> SolverInputs:
        details = portfolio.details
        if details.benchmark_id not in self.targets:
            msg = f"portfolio {details.portfolio_id}: benchmark {details.benchmark_id} is not loaded"
            raise LookupError(msg)
        # Every portfolio in a process reads the same market, and each solve of a portfolio its frames. Shallow copies cost
        # nothing under copy-on-write, and keep a rule that edits a frame in place from changing what the next solve sees.
        own = {name: value.copy(deep=False) for name, value in vars(portfolio).items() if isinstance(value, pd.DataFrame)}
        target = self.targets[details.benchmark_id]
        return SolverInputs(self.as_of_date, replace(portfolio, **own), self.securities.copy(deep=False), target.copy(deep=False))


def load_frames(loader: Loader, portfolio_ids: Sequence[str], as_of_date: date) -> dict[str, pd.DataFrame]:
    """Every dataset a run reads, by name, all loading at once: the market for `as_of_date`, and the portfolios, their
    lots and their loss sales of the 30 days to it, in batches. No portfolio ids loads empty portfolio frames."""
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    with TaskGroup() as group:
        market = group.submit(_load_market, loader, as_of_date)
        portfolios = group.submit(_load_portfolios, loader, portfolio_ids, as_of_date)
    return market.result() | portfolios.result()


def _load_market(loader: Loader, as_of_date: date) -> dict[str, pd.DataFrame]:
    with timed(log, "loaded market", as_of_date=as_of_date):
        return loader.load_all(*MARKET, as_of_date=as_of_date)


def _load_portfolios(loader: Loader, portfolio_ids: list[str], as_of_date: date) -> dict[str, pd.DataFrame]:
    with timed(log, "loaded portfolios", portfolios=len(portfolio_ids)) as fields:
        with TaskGroup() as group:
            portfolios = group.submit(loader.load_batched, PORTFOLIOS, "portfolios", portfolio_ids)
            lots = group.submit(loader.load_batched, LOTS, "portfolios", portfolio_ids)
            loss_sales = group.submit(loader.load_batched, LOSS_SALES, "portfolios", portfolio_ids, as_of_date=as_of_date)
        fields.update(lots=len(lots.result()), loss_sales=len(loss_sales.result()))
    return {"portfolios": portfolios.result(), "lots": lots.result(), "loss_sales": loss_sales.result()}


def build_market(frames: Frames, as_of_date: date) -> Market:
    """Join the per-security datasets into one row per bond with analytics, and check that the datasets agree. Besides
    the `Security` columns, each row has:

    - `offer_par`: par on offer today; null when the bond is not offered.
    - `expected_return`: the signal, annual and pre-tax; 0 without one, since the solver needs a number for every bond
      it holds.
    - `buyable`: offered, and it has a signal. Offered is a fact; needing a signal is house policy, never to buy a bond
      we have no view on. Rules can only narrow it, so it is the widest set any strategy may buy from.

    A market dataset with no rows, or a benchmark bond without analytics, fails the run: solving would trade on part of
    the market."""
    with timed(log, "built market", as_of_date=as_of_date) as fields:
        analytics, universe, signals, benchmarks = frames["securities"], frames["universe"], frames["signals"], frames["benchmarks"]
        if empty := [dataset.name for dataset in MARKET if frames[dataset.name].empty]:  # an empty signals reply would leave nothing buyable, and still trade
            msg = f"{', '.join(empty)} returned no rows for {as_of_date}: is it a business day, and has every feed landed? Solving without them would trade on part of the market"
            raise ValueError(msg)

        # get_indexer, not isin: pandas 3 builds a pyarrow scalar per value for isin on strings, ~300ms per 100k cusips
        if (unpriced := pd.Index(analytics["cusip"]).get_indexer(pd.Index(universe["cusip"])) < 0).any():
            log.warning("%d offerings have no analytics and cannot be bought", unpriced.sum(), extra={"cusips": universe.loc[unpriced, "cusip"].head(20).tolist()})
        securities = (
            analytics.merge(universe, on="cusip", how="left", validate="one_to_one")
            .merge(signals, on="cusip", how="left", validate="one_to_one")
            .assign(buyable=lambda s: s["offer_par"].notna() & s["expected_return"].notna(), expected_return=lambda s: s["expected_return"].fillna(0.0))
        )
        # A target we cannot price would silently shift benchmark duration and tracking, so fail loudly.
        if (orphans := pd.Index(securities["cusip"]).get_indexer(pd.Index(benchmarks["cusip"])) < 0).any():
            msg = f"{orphans.sum()} benchmark constituents have no analytics, e.g. {benchmarks.loc[orphans].head(3).to_dict('records')}"
            raise ValueError(msg)
        targets = {str(benchmark_id): group.set_index("cusip")["weight"] for benchmark_id, group in benchmarks.groupby("benchmark_id")}
        fields.update(securities=len(securities), buyable=int(securities["buyable"].sum()), benchmarks=len(targets))
    return Market(as_of_date, securities, targets)


def build_portfolios(frames: Frames, portfolio_ids: Sequence[str]) -> list[Portfolio]:
    """Each portfolio once, in the order given, with its lots and loss sales; a portfolio with none has an empty frame."""
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    details = {d.portfolio_id: d for d in to_models(PortfolioDetails, frames["portfolios"])}
    if missing := [i for i in portfolio_ids if i not in details]:
        msg = f"{len(missing)} unknown portfolios, e.g. {missing[:5]}"
        raise LookupError(msg)
    lots_of, sales_of = _by_portfolio(frames["lots"]), _by_portfolio(frames["loss_sales"])
    no_lots, no_sales = empty_frame(Lot), empty_frame(LossSale)
    return [Portfolio(details[i], lots_of.get(i, no_lots), sales_of.get(i, no_sales)) for i in portfolio_ids]


def _by_portfolio(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(portfolio_id): group.reset_index(drop=True) for portfolio_id, group in frame.groupby("portfolio_id")}
