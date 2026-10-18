"""Build a model portfolio from cash: the same engine, run for an all-cash portfolio that pays no tax."""

from datetime import date

from pydantic import Field

from optim import logs
from optim.context import Context
from optim.data.frames import empty_frame
from optim.datasets.portfolios import LossSale, Lot, PortfolioDetails
from optim.engine import Task, solve_all
from optim.inputs import Portfolio, load_market
from optim.jobs import Run
from optim.outputs.snapshots import save_run
from optim.schema import Model
from optim.strategy import Strategy


class ModelPortfolioRequest(Model):
    strategy: Strategy
    benchmark_id: str
    notional: float = Field(gt=0)
    as_of: date


def model_portfolio(ctx: Context, request: ModelPortfolioRequest) -> Run:
    run_id = ctx.new_run_id()
    logs.bind(run_id=run_id)
    market = load_market(ctx.loader, request.as_of)
    details = PortfolioDetails(
        portfolio_id=f"model:{request.strategy.name}", benchmark_id=request.benchmark_id, cash=request.notional, federal_rate=0.0, state_rate=0.0, st_gain_rate=0.0, lt_gain_rate=0.0
    )
    portfolios = [Portfolio(details, empty_frame(Lot), empty_frame(LossSale))]
    results = solve_all(market, [Task(portfolios[0], request.strategy)], workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="model_portfolio", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)
