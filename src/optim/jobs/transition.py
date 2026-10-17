"""How close to its target an account can get for each tax budget: one solve per budget, read as a frontier."""

import uuid
from datetime import date
from typing import Annotated

from pydantic import Field

from optim.context import Context
from optim.engine import Task, solve_all
from optim.inputs import load_market, load_portfolios
from optim.optimizer.constraints.tax import MaxTax
from optim.optimizer.results import Result
from optim.outputs.snapshots import save_run
from optim.schema import Strict
from optim.strategy import Strategy


class TransitionRequest(Strict):
    strategy: Strategy
    account_id: str
    tax_budgets: tuple[Annotated[float, Field(ge=0)], ...] = Field(min_length=1)  # dollars of tax allowed, one solve each
    as_of: date
    workers: int = Field(default=1, ge=1)


def transition(ctx: Context, request: TransitionRequest) -> list[Result]:
    run_id = str(uuid.uuid7())
    market = load_market(ctx.loader, request.as_of)
    portfolios = load_portfolios(ctx.loader, [request.account_id])
    tasks = [Task(portfolios[0], _with_tax_budget(request.strategy, budget)) for budget in request.tax_budgets]
    results = solve_all(market, tasks, workers=request.workers)
    save_run(ctx.snapshot_dir, run_id, job="transition", request=request, market=market, portfolios=portfolios, results=results)
    return results


def _with_tax_budget(strategy: Strategy, dollars: float) -> Strategy:
    return Strategy(name=f"{strategy.name}|tax<={dollars:.0f}", rules=strategy.rules, objective=strategy.objective, constraints=(*strategy.constraints, MaxTax(dollars=dollars)))
