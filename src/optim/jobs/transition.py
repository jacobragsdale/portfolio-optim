"""How close to its target a portfolio can get for each tax budget: one solve per budget, read as a frontier."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from optim.engine import SolveTask
from optim.inputs import Frames, build_portfolios
from optim.optimizer.constraints.tax import MaxTax
from optim.schema import Model
from optim.strategy import Strategy


class TransitionRequest(Model):
    job: Literal["transition"] = "transition"
    strategy: Strategy
    portfolio_id: str
    tax_budgets: list[Annotated[float, Field(ge=0)]] = Field(min_length=1)  # dollars of tax allowed, one solve each
    as_of_date: date

    @property
    def portfolio_ids(self) -> list[str]:
        return [self.portfolio_id]

    def tasks(self, frames: Frames) -> list[SolveTask]:
        [portfolio] = build_portfolios(frames, [self.portfolio_id])
        return [SolveTask(portfolio, _with_tax_budget(self.strategy, budget)) for budget in self.tax_budgets]


def _with_tax_budget(strategy: Strategy, dollars: float) -> Strategy:
    return Strategy(name=f"{strategy.name}|tax<={dollars:.0f}", rules=strategy.rules, objective=strategy.objective, constraints=[*strategy.constraints, MaxTax(dollars=dollars)], solve=strategy.solve)
