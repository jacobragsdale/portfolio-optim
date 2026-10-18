"""Rebalance portfolios: one solve each."""

from datetime import date
from typing import Literal

from pydantic import Field

from optim.engine import SolveTask
from optim.inputs import Frames, build_portfolios
from optim.schema import Model
from optim.strategy import Strategy


class OptimizeRequest(Model):
    job: Literal["optimize"] = "optimize"
    strategy: Strategy
    portfolios: list[str] = Field(min_length=1)
    as_of_date: date

    @property
    def portfolio_ids(self) -> list[str]:
        return self.portfolios

    def tasks(self, frames: Frames) -> list[SolveTask]:
        return [SolveTask(p, self.strategy) for p in build_portfolios(frames, self.portfolios)]
