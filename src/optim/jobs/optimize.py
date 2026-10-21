"""Rebalance portfolios: one solve each."""

from datetime import date
from typing import Literal

from pydantic import Field

from optim.datasets.portfolios import HoldingsBasis
from optim.engine import SolveTask
from optim.prep import Frames
from optim.prep.portfolios import build_portfolios
from optim.schema import Id, Model
from optim.strategy import Strategy


class OptimizeRequest(Model):
    job: Literal["optimize"] = "optimize"
    strategy: Strategy
    portfolios: list[Id] = Field(min_length=1)
    as_of_date: date
    holdings: HoldingsBasis = "working"
    """`working`: holdings as of now, the start of the day plus what has traded since; `sod`: as of the start of the day,
    as a start-of-day batch runs."""

    @property
    def portfolio_ids(self) -> list[str]:
        return self.portfolios

    def tasks(self, frames: Frames) -> list[SolveTask]:
        return [SolveTask(p, self.strategy) for p in build_portfolios(frames, self.portfolios)]
