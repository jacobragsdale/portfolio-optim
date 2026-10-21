"""Prep: what a solve sees, made from the datasets a run loads (`optim.datasets.load`). Pure, the same for every
strategy, and the one place booked values become floats for the solve. `market.build_market` joins the per-security
datasets into the `Market` every portfolio shares, `portfolios.build_portfolios` splits the per-portfolio ones into
`Portfolio`s, and `portfolios.inputs_for` pairs one portfolio with the market as the `SolverInputs` that the strategy's
rules edit: its lines, its cash, and the controls it obeys whatever the strategy (`controls`). Replay runs the same prep
on a snapshot's frames. A new dataset is merged in `build_market` or split in `build_portfolios`."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from optim.datasets.portfolios import PortfolioDetails

type Frames = Mapping[str, pd.DataFrame]  # a dataset's name -> its frame, as loaded


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
    lines: pd.DataFrame  # see `portfolios.inputs_for`; rules edit `buyable`, `sellable` and `expected_return`
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
