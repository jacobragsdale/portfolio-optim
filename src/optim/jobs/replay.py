"""Re-solve a saved run's portfolios with another strategy. It reads only the run's snapshot: no database, no cache and
no outputs beyond its own snapshot, so strategies can be compared on exactly the same data, by people and agents without access to the sources."""

import uuid
from pathlib import Path

from pydantic import Field

from optim import logs
from optim.engine import Task, solve_all
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
    market, portfolios = load_run(request.snapshot)
    if request.portfolios is not None:
        saved = {p.details.portfolio_id for p in portfolios}
        if missing := [i for i in request.portfolios if i not in saved]:
            msg = f"{len(missing)} portfolios are not in {request.snapshot}, e.g. {missing[:5]}"
            raise LookupError(msg)
        portfolios = [p for p in portfolios if p.details.portfolio_id in request.portfolios]
    results = solve_all(market, [Task(p, request.strategy) for p in portfolios], workers=workers)
    save_run(request.snapshot.parent, run_id, job="replay", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)
