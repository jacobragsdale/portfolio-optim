"""Loading every dataset a run reads, one validated frame each: the market for the business date, and the portfolios,
in batches. The only I/O before the solve; the snapshot saves these frames as they are, booked values as exact
`Decimal`s, and `optim.prep` makes what a solve sees from them."""

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from itertools import batched

import pandas as pd

from optim.data.dataset import Dataset
from optim.data.frames import empty_frame
from optim.data.loader import DatasetRead, Loader, Reads, TaskGroup
from optim.datasets import MARKET, PER_PORTFOLIO
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, PORTFOLIOS, SLEEVES, HoldingsBasis
from optim.logs import timed

log = logging.getLogger(__name__)

BATCH = 1000  # portfolios per query


def load_frames(loader: Loader, portfolio_ids: Sequence[str], as_of_date: date, holdings: HoldingsBasis) -> tuple[dict[str, pd.DataFrame], dict[str, DatasetRead]]:
    """Every dataset a run reads, by name, all loading at once: the market for `as_of_date`, and the portfolios, in
    batches, with their holdings as of `holdings`. No portfolio ids loads empty portfolio frames, and reads none. Also
    returns where each dataset came from, for the run's snapshot."""
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    reads = Reads()
    loader = replace(loader, reads=reads)  # this run's loads alone
    with TaskGroup() as group:
        market = group.submit(_load_market, loader, as_of_date)
        portfolios = group.submit(_load_portfolios, loader, portfolio_ids, as_of_date, holdings)
    return market.result() | portfolios.result(), reads.by_dataset()


def _load_market(loader: Loader, as_of_date: date) -> dict[str, pd.DataFrame]:
    with timed(log, "loaded market", as_of_date=as_of_date):
        return loader.load_all(*MARKET, as_of_date=as_of_date)


def _load_portfolios(loader: Loader, portfolio_ids: list[str], as_of_date: date, holdings: HoldingsBasis) -> dict[str, pd.DataFrame]:
    """Batches load concurrently, as many at once as the source's limit allows."""
    with timed(log, "loaded portfolios", portfolios=len(portfolio_ids), basis=holdings) as fields:
        with TaskGroup() as group:
            batches = [group.submit(_load_batch, loader, batch, as_of_date, holdings) for batch in batched(portfolio_ids, BATCH, strict=False)]
        frames = {dataset.name: _concat(dataset, [batch.result()[dataset.name] for batch in batches]) for dataset in (PORTFOLIOS, *PER_PORTFOLIO)}
        fields.update(holdings=len(frames["holdings"]))
    return frames


def _load_batch(loader: Loader, portfolios: tuple[str, ...], as_of_date: date, holdings: HoldingsBasis) -> dict[str, pd.DataFrame]:
    """One batch of portfolios, read in one read-only transaction: a trade booked between two of these reads would
    otherwise leave a portfolio's holdings, cash and controls from different moments. The SQL unpacks the batch's ids
    with `OPENJSON`."""
    # TODO(port): the old system's per-portfolio queries load here, each with the params its SQL binds.
    with loader.read_only("mssql") as source:
        return {
            "portfolios": source.load(PORTFOLIOS, portfolios=portfolios),
            "sleeves": source.load(SLEEVES, portfolios=portfolios),
            "holdings": source.load(HOLDINGS, portfolios=portfolios, as_of_date=as_of_date, basis=holdings),
            "controls": source.load(CONTROLS, portfolios=portfolios, as_of_date=as_of_date),
            "cash_flows": source.load(CASH_FLOWS, portfolios=portfolios, as_of_date=as_of_date),
        }


def _concat(dataset: Dataset, frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return empty_frame(dataset.row)
    # each batch's key is checked as it loads; a query that ignores its batch's ids repeats rows across batches
    return dataset.unique(pd.concat(frames, ignore_index=True), why=" across batches: does the query filter on its batch's ids?")
