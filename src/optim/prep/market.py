"""The `Market`: the per-security datasets joined into one row per security, built once a run in the parent and shipped
once to each worker process."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from optim.datasets import MARKET
from optim.logs import timed
from optim.prep import Frames

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Market:
    """Data shared by every portfolio in a run: built once, shipped once to each worker process."""

    as_of_date: date
    securities: pd.DataFrame  # one row per security: see `build_market`
    ids: pd.Index  # `securities["security_id"]`, for lookups: its hash table is built once per process
    universes: Mapping[str, pd.DataFrame]  # universe_id -> `security_id` and `available` (NaN: no limit)
    benchmarks: Mapping[str, pd.Series]  # benchmark_id -> weight by security_id


def build_market(frames: Frames, as_of_date: date) -> Market:
    """Join the per-security datasets into one row per security, with prices as floats, and check that the datasets
    agree. Besides the `Security` columns, each row has:

    - `unit_value`: what one unit of quantity is worth, in its currency: `(price + accrued) / price_per`. Every value
      in the solve is a quantity times it.
    - `expected_return`: the signal, annual; 0 without one, since the solver needs a number for every line it holds.

    A market dataset with no rows, or a benchmark constituent without analytics, fails the run: solving would trade on
    part of the market. An eligible security without analytics cannot be bought, and is left out with a warning."""
    with timed(log, "built market", as_of_date=as_of_date) as fields:
        analytics, universe, signals, benchmarks = frames["securities"], frames["universe"], frames["signals"], frames["benchmarks"]
        if empty := [dataset.name for dataset in MARKET if frames[dataset.name].empty]:  # an empty signals reply would zero every return, and still trade
            msg = f"{', '.join(empty)} returned no rows for {as_of_date}: is it a business day, and has every feed landed? Solving without them would trade on part of the market"
            raise ValueError(msg)

        # TODO(port): joins and data-wide rules that every strategy needs go here. Whether a security without a signal
        # may be bought, and what return it gets, is the old optimizer's call: 0 is a placeholder.
        price, accrued = analytics["price"].to_numpy(dtype=float), analytics["accrued"].to_numpy(dtype=float)
        securities = analytics.assign(price=price, accrued=accrued, unit_value=(price + accrued) / analytics["price_per"].to_numpy()).merge(
            signals, on="security_id", how="left", validate="one_to_one"
        )
        securities["expected_return"] = securities["expected_return"].fillna(0.0)
        ids = pd.Index(securities["security_id"])
        # get_indexer, not isin: pandas 3 builds a pyarrow scalar per value for isin on strings, ~300ms per 100k ids
        if (unpriced := ids.get_indexer(pd.Index(universe["security_id"])) < 0).any():
            log.warning("%d eligible securities have no analytics and cannot be bought", unpriced.sum(), extra={"security_ids": universe.loc[unpriced, "security_id"].head(20).tolist()})
        eligible = universe.loc[~unpriced].assign(available=lambda u: u["available"].to_numpy(dtype=float, na_value=np.nan))
        universes = {str(universe_id): group[["security_id", "available"]].reset_index(drop=True) for universe_id, group in eligible.groupby("universe_id")}
        # A constituent we cannot price would silently shift tracking, so fail loudly.
        if (orphans := ids.get_indexer(pd.Index(benchmarks["security_id"])) < 0).any():
            msg = f"{orphans.sum()} benchmark constituents have no analytics, e.g. {benchmarks.loc[orphans].head(3).to_dict('records')}"
            raise ValueError(msg)
        weights = {str(benchmark_id): group.set_index("security_id")["weight"] for benchmark_id, group in benchmarks.groupby("benchmark_id")}
        fields.update(securities=len(securities), universes=len(universes), benchmarks=len(weights))
    return Market(as_of_date, securities, ids, universes, weights)
