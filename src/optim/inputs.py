"""What a solve sees, and how it is prepared from the datasets a run loads.

`load_frames` is the only I/O here: it loads every dataset a run reads, one validated frame each, and the snapshot saves
those frames as they are, booked values as exact `Decimal`s. The rest is prep, pure and the same for every strategy, and
the one place values become floats for the solve: `build_market` joins the per-security datasets into the `Market` every
portfolio shares, `build_portfolios` splits the per-portfolio ones into `Portfolio`s, and `Market.inputs_for` pairs one
portfolio with the market as the `SolverInputs` that the strategy's rules edit: its lines, its cash, and the controls it
obeys whatever the strategy. Replay runs the same prep on a snapshot's frames. A new dataset is loaded in `load_frames`,
then merged in `build_market` or split in `build_portfolios`."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from itertools import batched
from typing import Literal

import numpy as np
import pandas as pd

from optim.data.frames import empty_frame, to_models
from optim.data.loader import Dataset, DatasetRead, Loader, Reads, TaskGroup
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, PORTFOLIOS, SLEEVES, PortfolioDetails, Sleeve
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.logs import timed

log = logging.getLogger(__name__)

type Frames = Mapping[str, pd.DataFrame]  # a dataset's name -> its frame, as loaded
type HoldingsBasis = Literal["working", "sod"]  # start of day, or the start of day plus what has traded since

# TODO(port): the old system's per-security datasets join this tuple, and are merged in `build_market`.
MARKET = (SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS)  # loaded once a run, for its business date
# TODO(port): the old system's per-portfolio datasets join this tuple, are loaded in `_load_batch`, and are fields of
# `Portfolio`, named as the dataset is.
PER_PORTFOLIO = (SLEEVES, HOLDINGS, CONTROLS, CASH_FLOWS)  # each portfolio's rows, beside its `PORTFOLIOS` row
BATCH = 1000  # portfolios per query


@dataclass(frozen=True)
class Portfolio:
    """One portfolio's rows of each per-portfolio dataset, as loaded."""

    details: PortfolioDetails
    sleeves: pd.DataFrame  # `Sleeve` columns
    holdings: pd.DataFrame  # `Holding` columns, cash included
    controls: pd.DataFrame  # `Control` columns
    cash_flows: pd.DataFrame  # `CashFlow` columns


@dataclass(frozen=True)
class SolverInputs:
    """Everything one solve sees. Rules return edited copies; nothing is mutated in place."""

    as_of_date: date
    portfolio: Portfolio  # as loaded
    lines: pd.DataFrame  # see `Market.inputs_for`; rules edit `buyable`, `sellable` and `expected_return`
    cash: float  # the portfolio's cash holdings, in its currency, before trading and cash flows
    cash_flow: float  # its cash flows, summed: positive to invest, negative to raise
    securities: pd.DataFrame  # the whole market, by `build_market`, for benchmark constituents no line holds
    benchmarks: Mapping[str, pd.Series]  # benchmark_id -> weight by security_id

    def restrict_buys(self, allowed: pd.Series) -> SolverInputs:
        """Narrow what may be bought, by line. Rows stay: a security you may not buy can still be held, and sold."""
        return replace(self, lines=self.lines.assign(buyable=self.lines["buyable"] & allowed))

    def restrict_sales(self, allowed: pd.Series) -> SolverInputs:
        """Narrow what may be sold at the strategy's discretion, by line. A mandatory sale stays: a strategy cannot
        overrule a control. Rows stay: a line you may not sell is still held, and counts as it does."""
        lines = self.lines
        return replace(self, lines=lines.assign(sellable=lines["sellable"] & (allowed | lines["must_sell"])))


@dataclass(frozen=True)
class Market:
    """Data shared by every portfolio in a run: built once, shipped once to each worker process."""

    as_of_date: date
    securities: pd.DataFrame  # one row per security: see `build_market`
    ids: pd.Index  # `securities["security_id"]`, for lookups: its hash table is built once per process
    universes: Mapping[str, pd.DataFrame]  # universe_id -> `security_id` and `available` (NaN: no limit)
    benchmarks: Mapping[str, pd.Series]  # benchmark_id -> weight by security_id

    def inputs_for(self, portfolio: Portfolio) -> SolverInputs:
        """The market as one portfolio sees it, before the strategy's rules. A holding of a security or a sleeve that is
        not loaded, a security in another currency, or controls that conflict or name an unknown security fail the
        portfolio: solving would trade on a guess.

        Its `lines` are one row per sleeve and security: every holding but cash, and every security on a sleeve's
        eligibility list. Besides the security's columns (`build_market`), each line has:

        - `sleeve_id`, and `held`: the quantity held before trading, as loaded (`Decimal`); 0 for a line only listed.
        - `available`: quantity on offer, where the list is an inventory; NaN otherwise.
        - `buyable`: on its sleeve's list, in the portfolio's currency, traded in round lots, and no control forbids it.
          Rules can only narrow it.
        - `sellable`: held, and no control forbids selling it at the optimizer's discretion.
        - `must_sell`: a control requires the whole holding sold. It stays sellable whatever a rule says.
        """
        details = portfolio.details
        held = self._held(portfolio)
        is_cash = (held["asset_class"] == "cash").to_numpy()
        cash = float(held.loc[is_cash, "quantity"].to_numpy(dtype=float) @ held.loc[is_cash, "unit_value"].to_numpy())
        lines = _controlled(self._lines(portfolio, held.loc[~is_cash]), portfolio, self.ids)
        for benchmark in portfolio.sleeves["benchmark_id"].dropna():
            if benchmark not in self.benchmarks:
                msg = f"portfolio {details.portfolio_id}: benchmark {benchmark} is not loaded"
                raise LookupError(msg)
        # Every portfolio in a process reads the same market, and each solve of a portfolio its frames. Shallow copies cost
        # nothing under copy-on-write, and keep a rule that edits a frame in place from changing what the next solve sees.
        own = {name: value.copy(deep=False) for name, value in vars(portfolio).items() if isinstance(value, pd.DataFrame)}
        cash_flow = float(sum(portfolio.cash_flows["amount"], Decimal(0)))
        # TODO(port): prep that every strategy needs and that depends on the portfolio goes here, such as what a
        # portfolio's restrictions mean for its lines. Prep that only some strategies need is a rule instead.
        return SolverInputs(self.as_of_date, replace(portfolio, **own), lines, cash, cash_flow, self.securities.copy(deep=False), self.benchmarks)

    def _held(self, portfolio: Portfolio) -> pd.DataFrame:
        """The portfolio's holdings, each with its security's columns."""
        details, holdings = portfolio.details, portfolio.holdings
        rows = self.ids.get_indexer(pd.Index(holdings["security_id"]))
        if (rows < 0).any():
            msg = f"portfolio {details.portfolio_id} holds securities with no analytics: {holdings.loc[rows < 0, 'security_id'].head().tolist()}"
            raise LookupError(msg)
        if unknown := set(holdings["sleeve_id"]) - set(portfolio.sleeves["sleeve_id"]):
            msg = f"portfolio {details.portfolio_id} holds positions in sleeves it does not have: {sorted(unknown)[:5]}"
            raise LookupError(msg)
        held = pd.concat([holdings.reset_index(drop=True), self.securities.iloc[rows].drop(columns="security_id").reset_index(drop=True)], axis=1)
        if (short := (held["asset_class"] != "cash").to_numpy() & (held["quantity"] < 0).to_numpy(dtype=bool)).any():
            msg = (
                f"portfolio {details.portfolio_id} holds negative quantities of securities that are not cash, e.g. {held.loc[short, 'security_id'].head(3).tolist()}: short positions are not modelled"
            )
            raise ValueError(msg)
        if (foreign := held["currency"] != details.currency).any():
            examples = held.loc[foreign, "security_id"].head(3).tolist()
            msg = f"portfolio {details.portfolio_id} holds securities in another currency than its {details.currency}, e.g. {examples}: there are no FX rates to value them"
            raise ValueError(msg)
        return held

    def _lines(self, portfolio: Portfolio, held: pd.DataFrame) -> pd.DataFrame:
        listed = []
        for sleeve in to_models(Sleeve, portfolio.sleeves):
            if sleeve.universe_id is None:
                continue
            if sleeve.universe_id not in self.universes:
                msg = f"portfolio {portfolio.details.portfolio_id}: sleeve {sleeve.sleeve_id}'s eligibility list {sleeve.universe_id} is not loaded"
                raise LookupError(msg)
            listed.append(self.universes[sleeve.universe_id].assign(sleeve_id=sleeve.sleeve_id))
        eligible = pd.concat(listed, ignore_index=True) if listed else pd.DataFrame({"security_id": pd.Series(dtype="str"), "available": pd.Series(dtype=float), "sleeve_id": pd.Series(dtype="str")})
        holdings = held[["sleeve_id", "security_id", "quantity"]].rename(columns={"quantity": "held"})
        pairs = holdings.merge(eligible, on=["sleeve_id", "security_id"], how="outer", validate="one_to_one", indicator="listed")
        rows = self.ids.get_indexer(pd.Index(pairs["security_id"]))
        lines = pd.concat([pairs.reset_index(drop=True), self.securities.iloc[rows].drop(columns="security_id").reset_index(drop=True)], axis=1)
        held_qty = lines["held"].where(lines["held"].notna(), Decimal(0))
        buyable = (lines["listed"] != "left_only") & (lines["asset_class"] != "cash") & lines["increment"].notna() & (lines["currency"] == portfolio.details.currency)
        return lines.drop(columns="listed").assign(held=held_qty, buyable=buyable.to_numpy(), sellable=(held_qty > 0).to_numpy(dtype=bool), must_sell=False)


def _controlled(lines: pd.DataFrame, portfolio: Portfolio, known: pd.Index) -> pd.DataFrame:
    """`lines` with the portfolio's controls applied. A control that names an unknown security fails the portfolio, as
    do controls that disagree: each is how a control silently stops applying. A mandatory sale of a security the
    portfolio no longer holds has happened, as in a working run after its trade; it still stops the security being
    bought back."""
    portfolio_id, controls = portfolio.details.portfolio_id, portfolio.controls
    named = controls["security_id"].dropna()
    if (unknown := known.get_indexer(pd.Index(named)) < 0).any():
        msg = f"portfolio {portfolio_id}: controls name securities that are not loaded, e.g. {named[unknown].head(3).tolist()}; a control that matches nothing would not apply"
        raise LookupError(msg)

    def applies(*actions: str) -> np.ndarray:
        these = controls[controls["action"].isin(actions)]
        return lines["security_id"].isin(these["security_id"].dropna()).to_numpy() | bool(these["security_id"].isna().any())

    held = lines["sellable"].to_numpy()  # every held line, before any control
    must, blocked = applies("must_sell") & held, applies("no_sell", "hold") & held
    if conflict := sorted(set(lines.loc[must & blocked, "security_id"])):
        involved = controls[controls["security_id"].isin(conflict) | controls["security_id"].isna()]
        msg = f"portfolio {portfolio_id}: controls both require and forbid selling {conflict[:5]}: {_quote(involved)}"
        raise ValueError(msg)
    mandatory = controls.loc[controls["action"].eq("must_sell") & controls["security_id"].notna(), "security_id"]
    if not (unheld := mandatory[~mandatory.isin(lines.loc[held, "security_id"])]).empty:
        log.warning(
            "%s: mandatory sales of securities it no longer holds are done", portfolio_id, extra={"portfolio_id": portfolio_id, "controls": _quote(controls[controls["security_id"].isin(unheld)])}
        )
    return lines.assign(buyable=lines["buyable"] & ~applies("no_buy", "hold", "must_sell"), sellable=held & ~blocked, must_sell=must)


def _quote(controls: pd.DataFrame) -> str:
    return "; ".join(f"{c.control_id} {c.action} {c.security_id if isinstance(c.security_id, str) else 'every security'} ({c.reason})" for c in controls.itertuples())


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


def build_portfolios(frames: Frames, portfolio_ids: Sequence[str]) -> list[Portfolio]:
    """Each portfolio once, in the order given, with its rows of each per-portfolio dataset; none is an empty frame."""
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    details = {d.portfolio_id: d for d in to_models(PortfolioDetails, frames["portfolios"])}
    if missing := [i for i in portfolio_ids if i not in details]:
        msg = f"{len(missing)} unknown portfolios, e.g. {missing[:5]}"
        raise LookupError(msg)
    split = {dataset.name: (_by_portfolio(frames[dataset.name]), empty_frame(dataset.row)) for dataset in PER_PORTFOLIO}
    return [Portfolio(details[i], **{name: rows.get(i, none) for name, (rows, none) in split.items()}) for i in portfolio_ids]


def _by_portfolio(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(portfolio_id): group.reset_index(drop=True) for portfolio_id, group in frame.groupby("portfolio_id")}
