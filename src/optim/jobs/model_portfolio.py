"""Build a model portfolio from cash: the same engine, run for an all-cash account that pays no tax."""

import uuid

from pydantic import Field

from optim.context import Context
from optim.data.frames import empty_frame
from optim.datasets.accounts import Account, Lot
from optim.engine import Task, solve_all
from optim.inputs import Portfolio, load_market
from optim.jobs import Run
from optim.outputs.snapshots import save_run
from optim.schema import Date, Strict
from optim.strategy import Strategy


class ModelPortfolioRequest(Strict):
    strategy: Strategy
    benchmark_id: str
    notional: float = Field(gt=0)
    as_of: Date  # accepts an ISO string, which strict mode refuses for `date` in Python mode


def model_portfolio(ctx: Context, request: ModelPortfolioRequest) -> Run:
    run_id = str(uuid.uuid7())
    market = load_market(ctx.loader, request.as_of)
    account = Account(account_id=f"model:{request.strategy.name}", benchmark_id=request.benchmark_id, cash=request.notional, federal_rate=0.0, state_rate=0.0, st_gain_rate=0.0, lt_gain_rate=0.0)
    portfolios = [Portfolio(account, empty_frame(Lot))]
    results = solve_all(market, [Task(portfolios[0], request.strategy)], workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="model_portfolio", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)
