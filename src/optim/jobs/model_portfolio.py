"""Build a model portfolio from cash: the same engine, run for an all-cash portfolio that pays no tax."""

from datetime import date
from typing import Literal

from pydantic import Field

from optim.datasets.portfolios import PORTFOLIOS, PortfolioDetails
from optim.engine import SolveTask
from optim.inputs import Frames, build_portfolios
from optim.schema import Model
from optim.strategy import Strategy


class ModelPortfolioRequest(Model):
    job: Literal["model_portfolio"] = "model_portfolio"
    strategy: Strategy
    benchmark_id: str
    notional: float = Field(gt=0)
    as_of_date: date

    @property
    def portfolio_ids(self) -> list[str]:
        return []  # nothing to load: the portfolio is made from the request

    def tasks(self, frames: Frames) -> list[SolveTask]:
        details = PortfolioDetails(portfolio_id=f"model:{self.strategy.name}", benchmark_id=self.benchmark_id, cash=self.notional, federal_rate=0.0, state_rate=0.0, st_gain_rate=0.0, lt_gain_rate=0.0)
        # it stands as the only portfolio, beside the empty lots and loss sales the run loaded for no portfolios
        frames = {**frames, "portfolios": PORTFOLIOS.to_frame({k: [v] for k, v in details.model_dump().items()})}
        return [SolveTask(portfolio, self.strategy) for portfolio in build_portfolios(frames, [details.portfolio_id])]
