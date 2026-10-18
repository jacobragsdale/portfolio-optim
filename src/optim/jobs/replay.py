"""Re-solve a saved run's portfolios with another strategy. It reads only the run's snapshot: no database, no cache and
no outputs beyond its own snapshot, so strategies can be compared on exactly the same data, by people and agents without access to the sources.
It prepares the solves from the saved datasets with the same code as a live run, so a change to that code shows here too."""

import uuid
from pathlib import Path

from pydantic import Field

from optim import logs
from optim.engine import SolveTask, solve_all
from optim.inputs import build_inputs
from optim.jobs import Run
from optim.outputs.snapshots import load_run, save_run
from optim.schema import Model
from optim.strategy import Strategy


class ReplayRequest(Model):
    strategy: Strategy
    snapshot: Path  # the run's snapshot directory
    portfolios: list[str] | None = Field(default=None, min_length=1)  # a subset of the run's portfolios; None replays all of them


def replay(request: ReplayRequest, *, workers: int) -> Run:
    """Saved as a new run, beside the run it replays."""
    run_id = str(uuid.uuid7())  # replays touch no shared system, so they are not registered in the runs table
    logs.bind(run_id=run_id)
    as_of_date, frames = load_run(request.snapshot)
    portfolio_ids = request.portfolios or frames["portfolios"]["portfolio_id"].tolist()
    market, portfolios = build_inputs(frames, portfolio_ids, as_of_date)
    results = solve_all(market, [SolveTask(p, request.strategy) for p in portfolios], workers=workers)
    save_run(request.snapshot.parent, run_id, job="replay", request=request, as_of_date=as_of_date, frames=frames, results=results)
    return Run(run_id, results)
