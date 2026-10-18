"""Rebalance portfolios."""

from datetime import date

from pydantic import Field

from optim import logs
from optim.context import Context
from optim.engine import SolveTask, solve_all
from optim.inputs import load_market, load_portfolios
from optim.jobs import Run
from optim.outputs.snapshots import save_run
from optim.schema import Model
from optim.strategy import Strategy


class OptimizeRequest(Model):
    strategy: Strategy
    portfolios: list[str] = Field(min_length=1)
    as_of_date: date


def optimize(ctx: Context, request: OptimizeRequest) -> Run:
    run_id = ctx.new_run_id()
    logs.bind(run_id=run_id)
    market = load_market(ctx.loader, request.as_of_date)
    portfolios = load_portfolios(ctx.loader, request.portfolios, request.as_of_date)
    results = solve_all(market, [SolveTask(p, request.strategy) for p in portfolios], workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="optimize", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)
