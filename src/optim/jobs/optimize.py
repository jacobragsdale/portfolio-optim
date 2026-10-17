"""Rebalance accounts, and submit their orders."""

import uuid

from pydantic import Field

from optim.context import Context
from optim.engine import Task, solve_all
from optim.inputs import load_market, load_portfolios
from optim.jobs import Run
from optim.outputs.oms import submit_orders
from optim.outputs.orders import orders_for
from optim.outputs.snapshots import save_run
from optim.schema import Date, Strict
from optim.strategy import Strategy


class OptimizeRequest(Strict):
    strategy: Strategy
    accounts: list[str] = Field(min_length=1)
    as_of: Date  # accepts an ISO string, which strict mode refuses for `date` in Python mode
    submit: bool = False  # a dry run unless set


def optimize(ctx: Context, request: OptimizeRequest) -> Run:
    """Orders go out only for results that rounding left intact, and only after the run's snapshot is saved."""
    run_id = str(uuid.uuid7())
    market = load_market(ctx.loader, request.as_of)
    portfolios = load_portfolios(ctx.loader, request.accounts)
    results = solve_all(market, [Task(p, request.strategy) for p in portfolios], workers=ctx.workers)
    orders = orders_for(run_id, results)
    save_run(ctx.snapshot_dir, run_id, job="optimize", request=request, market=market, portfolios=portfolios, results=results, orders=orders)
    if request.submit and orders:
        submit_orders(ctx.oms, orders)
    return Run(run_id, results)
