"""Solve portfolios that are not in the books, such as a prospect's, given whole in the request: one solve each. The
request carries their rows, so the run's snapshot records them, and replay solves them again."""

from datetime import date
from typing import Literal, Self

import pandas as pd
from pydantic import Field, model_validator

from optim.data.dataset import Dataset
from optim.datasets import PER_PORTFOLIO
from optim.datasets.controls import Control
from optim.datasets.portfolios import PORTFOLIOS, CashFlow, Holding, HoldingsBasis, PortfolioDetails, Sleeve
from optim.engine import SolveTask
from optim.prep import Frames
from optim.prep.portfolios import build_portfolios
from optim.schema import Model, Row
from optim.strategy import Strategy


class HypotheticalPortfolio(Model):
    """A portfolio as the per-portfolio datasets would hold it, a field per dataset."""

    details: PortfolioDetails
    sleeves: list[Sleeve] = Field(min_length=1)
    holdings: list[Holding] = Field(default_factory=list)
    """Cash included, as a holding of a cash security."""
    controls: list[Control] = Field(default_factory=list)
    cash_flows: list[CashFlow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_portfolio(self) -> Self:
        ids = {row.portfolio_id for row in self.sleeves} | {row.portfolio_id for row in self.holdings} | {row.portfolio_id for row in self.controls} | {row.portfolio_id for row in self.cash_flows}
        if others := ids - {self.details.portfolio_id}:
            msg = f"portfolio {self.details.portfolio_id} has rows of other portfolios: {sorted(others)}"
            raise ValueError(msg)
        return self


class HypotheticalRequest(Model):
    job: Literal["hypothetical"] = "hypothetical"
    strategy: Strategy
    portfolios: list[HypotheticalPortfolio] = Field(min_length=1)
    as_of_date: date

    @property
    def portfolio_ids(self) -> list[str]:
        return []  # it loads none: the request holds them

    @property
    def holdings(self) -> HoldingsBasis:
        return "working"  # it loads no holdings

    def tasks(self, frames: Frames) -> list[SolveTask]:
        given = {name: [row for p in self.portfolios for row in getattr(p, name)] for name in ("sleeves", "holdings", "controls", "cash_flows")}
        given["portfolios"] = [p.details for p in self.portfolios]
        frames = {**frames} | {dataset.name: _with(dataset, frames[dataset.name], given[dataset.name]) for dataset in (PORTFOLIOS, *PER_PORTFOLIO)}
        return [SolveTask(p, self.strategy) for p in build_portfolios(frames, [p.details.portfolio_id for p in self.portfolios])]


def _with(dataset: Dataset, loaded: pd.DataFrame, rows: list[Row]) -> pd.DataFrame:
    """`loaded` and `rows`, validated as the dataset validates a load, with its key checked across both."""
    added = dataset.to_frame({name: [getattr(row, name) for row in rows] for name in dataset.row.model_fields})
    return dataset.unique(pd.concat([loaded, added], ignore_index=True), why=": a hypothetical portfolio's id is also a loaded portfolio's")
