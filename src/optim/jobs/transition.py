"""How close to its target a portfolio can get for each tax budget: one solve per budget, read as a frontier."""

from datetime import date
from typing import Annotated

from pydantic import Field

from optim import logs
from optim.context import Context
from optim.engine import SolveTask, solve_all
from optim.inputs import load_inputs
from optim.jobs import Run
from optim.optimizer.constraints.tax import MaxTax
from optim.outputs.snapshots import save_run
from optim.schema import Model
from optim.strategy import Strategy


class TransitionRequest(Model):
    strategy: Strategy
    portfolio_id: str
    tax_budgets: list[Annotated[float, Field(ge=0)]] = Field(min_length=1)  # dollars of tax allowed, one solve each
    as_of_date: date


def transition(ctx: Context, request: TransitionRequest) -> Run:
    run_id = ctx.new_run_id()
    logs.bind(run_id=run_id)
    market, portfolios = load_inputs(ctx.loader, [request.portfolio_id], request.as_of_date)
    tasks = [SolveTask(portfolios[0], _with_tax_budget(request.strategy, budget)) for budget in request.tax_budgets]
    results = solve_all(market, tasks, workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="transition", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)


def _with_tax_budget(strategy: Strategy, dollars: float) -> Strategy:
    return Strategy(name=f"{strategy.name}|tax<={dollars:.0f}", rules=strategy.rules, objective=strategy.objective, constraints=[*strategy.constraints, MaxTax(dollars=dollars)], solve=strategy.solve)
