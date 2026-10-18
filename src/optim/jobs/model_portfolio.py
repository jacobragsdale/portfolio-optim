"""Build a model portfolio from cash: the same engine, run for an all-cash portfolio that pays no tax."""

from datetime import date

from pydantic import Field

from optim import logs
from optim.context import Context
from optim.datasets.portfolios import PORTFOLIOS, PortfolioDetails
from optim.engine import SolveTask, solve_all
from optim.inputs import build_inputs, load_frames
from optim.jobs import Run
from optim.outputs.snapshots import save_run
from optim.schema import Model
from optim.strategy import Strategy


class ModelPortfolioRequest(Model):
    strategy: Strategy
    benchmark_id: str
    notional: float = Field(gt=0)
    as_of_date: date


def model_portfolio(ctx: Context, request: ModelPortfolioRequest) -> Run:
    run_id = ctx.new_run_id()
    logs.bind(run_id=run_id)
    details = PortfolioDetails(
        portfolio_id=f"model:{request.strategy.name}", benchmark_id=request.benchmark_id, cash=request.notional, federal_rate=0.0, state_rate=0.0, st_gain_rate=0.0, lt_gain_rate=0.0
    )
    # the market, and empty portfolio frames in which the model portfolio stands as the only portfolio
    frames = load_frames(ctx.loader, [], request.as_of_date) | {"portfolios": PORTFOLIOS.to_frame({k: [v] for k, v in details.model_dump().items()})}
    market, portfolios = build_inputs(frames, [details.portfolio_id], request.as_of_date)
    results = solve_all(market, [SolveTask(portfolios[0], request.strategy)], workers=ctx.workers)
    save_run(ctx.snapshot_dir, run_id, job="model_portfolio", request=request, as_of_date=request.as_of_date, frames=frames, results=results)
    return Run(run_id, results)
