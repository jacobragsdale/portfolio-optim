"""How close to its target an account can get for each tax budget: one solve per budget, read as a frontier."""

import uuid
from typing import Annotated

from pydantic import Field

from optim.context import Context
from optim.engine import Task, solve_all
from optim.inputs import load_market, load_portfolios
from optim.jobs import Run
from optim.optimizer.constraints.tax import MaxTax
from optim.outputs.snapshots import save_run
from optim.schema import Date, Strict
from optim.strategy import Strategy


class TransitionRequest(Strict):
    strategy: Strategy
    account_id: str
    tax_budgets: list[Annotated[float, Field(ge=0)]] = Field(min_length=1)  # dollars of tax allowed, one solve each
    as_of: Date  # accepts an ISO string, which strict mode refuses for `date` in Python mode


def transition(ctx: Context, request: TransitionRequest) -> Run:
    run_id = str(uuid.uuid7())
    market = load_market(ctx.loader, request.as_of)
    portfolios = load_portfolios(ctx.loader, [request.account_id])
    tasks = [Task(portfolios[0], _with_tax_budget(request.strategy, budget)) for budget in request.tax_budgets]
    results = solve_all(market, tasks, workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="transition", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)


def _with_tax_budget(strategy: Strategy, dollars: float) -> Strategy:
    return Strategy(name=f"{strategy.name}|tax<={dollars:.0f}", rules=strategy.rules, objective=strategy.objective, constraints=[*strategy.constraints, MaxTax(dollars=dollars)], solve=strategy.solve)
