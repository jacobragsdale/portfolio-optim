"""Each portfolio's rows of the per-portfolio datasets (`build_portfolios`), and the market as one portfolio sees it
(`inputs_for`): its lines, its cash, and its controls applied."""

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pandas as pd

from optim.data.frames import empty_frame, to_models
from optim.datasets import PER_PORTFOLIO
from optim.datasets.portfolios import PortfolioDetails, Sleeve
from optim.prep import Frames, Portfolio, SolverInputs
from optim.prep.controls import apply_controls
from optim.prep.market import Market


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


def inputs_for(market: Market, portfolio: Portfolio) -> SolverInputs:
    """The market as one portfolio sees it, before the strategy's rules. A holding of a security or a sleeve that is not
    loaded, a security in another currency, or controls that conflict or name an unknown security fail the portfolio:
    solving would trade on a guess.

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
    held = _held(market, portfolio)
    is_cash = (held["asset_class"] == "cash").to_numpy()
    cash = float(held.loc[is_cash, "quantity"].to_numpy(dtype=float) @ held.loc[is_cash, "unit_value"].to_numpy())
    lines = apply_controls(_lines(market, portfolio, held.loc[~is_cash]), portfolio, market.ids)
    for benchmark in portfolio.sleeves["benchmark_id"].dropna():
        if benchmark not in market.benchmarks:
            msg = f"portfolio {details.portfolio_id}: benchmark {benchmark} is not loaded"
            raise LookupError(msg)
    # Every portfolio in a process reads the same market, and each solve of a portfolio its frames. Shallow copies cost
    # nothing under copy-on-write, and keep a rule that edits a frame in place from changing what the next solve sees.
    own = {name: value.copy(deep=False) for name, value in vars(portfolio).items() if isinstance(value, pd.DataFrame)}
    cash_flow = float(sum(portfolio.cash_flows["amount"], Decimal(0)))
    # TODO(port): prep that every strategy needs and that depends on the portfolio goes here, such as what a
    # portfolio's restrictions mean for its lines. Prep that only some strategies need is a rule instead.
    return SolverInputs(market.as_of_date, replace(portfolio, **own), lines, cash, cash_flow, market.securities.copy(deep=False), market.benchmarks)


def _held(market: Market, portfolio: Portfolio) -> pd.DataFrame:
    """The portfolio's holdings, each with its security's columns."""
    details, holdings = portfolio.details, portfolio.holdings
    rows = market.ids.get_indexer(pd.Index(holdings["security_id"]))
    if (rows < 0).any():
        msg = f"portfolio {details.portfolio_id} holds securities with no analytics: {holdings.loc[rows < 0, 'security_id'].head().tolist()}"
        raise LookupError(msg)
    if unknown := set(holdings["sleeve_id"]) - set(portfolio.sleeves["sleeve_id"]):
        msg = f"portfolio {details.portfolio_id} holds positions in sleeves it does not have: {sorted(unknown)[:5]}"
        raise LookupError(msg)
    held = pd.concat([holdings.reset_index(drop=True), market.securities.iloc[rows].drop(columns="security_id").reset_index(drop=True)], axis=1)
    if (short := (held["asset_class"] != "cash").to_numpy() & (held["quantity"] < 0).to_numpy(dtype=bool)).any():
        msg = f"portfolio {details.portfolio_id} holds negative quantities of securities that are not cash, e.g. {held.loc[short, 'security_id'].head(3).tolist()}: short positions are not modelled"
        raise ValueError(msg)
    if (foreign := held["currency"] != details.currency).any():
        examples = held.loc[foreign, "security_id"].head(3).tolist()
        msg = f"portfolio {details.portfolio_id} holds securities in another currency than its {details.currency}, e.g. {examples}: there are no FX rates to value them"
        raise ValueError(msg)
    return held


def _lines(market: Market, portfolio: Portfolio, held: pd.DataFrame) -> pd.DataFrame:
    listed = []
    for sleeve in to_models(Sleeve, portfolio.sleeves):
        if sleeve.universe_id is None:
            continue
        if sleeve.universe_id not in market.universes:
            msg = f"portfolio {portfolio.details.portfolio_id}: sleeve {sleeve.sleeve_id}'s eligibility list {sleeve.universe_id} is not loaded"
            raise LookupError(msg)
        listed.append(market.universes[sleeve.universe_id].assign(sleeve_id=sleeve.sleeve_id))
    eligible = pd.concat(listed, ignore_index=True) if listed else pd.DataFrame({"security_id": pd.Series(dtype="str"), "available": pd.Series(dtype=float), "sleeve_id": pd.Series(dtype="str")})
    holdings = held[["sleeve_id", "security_id", "quantity"]].rename(columns={"quantity": "held"})
    pairs = holdings.merge(eligible, on=["sleeve_id", "security_id"], how="outer", validate="one_to_one", indicator="listed")
    rows = market.ids.get_indexer(pd.Index(pairs["security_id"]))
    lines = pd.concat([pairs.reset_index(drop=True), market.securities.iloc[rows].drop(columns="security_id").reset_index(drop=True)], axis=1)
    held_qty = lines["held"].where(lines["held"].notna(), Decimal(0))
    buyable = (lines["listed"] != "left_only") & (lines["asset_class"] != "cash") & lines["increment"].notna() & (lines["currency"] == portfolio.details.currency)
    return lines.drop(columns="listed").assign(held=held_qty, buyable=buyable.to_numpy(), sellable=(held_qty > 0).to_numpy(dtype=bool), must_sell=False)
