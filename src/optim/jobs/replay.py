"""Re-solve a saved run's portfolios with another strategy. It reads only the run's snapshot: no database, no cache and
no orders, so strategies can be compared on exactly the same data, by people and agents without access to the sources."""

import uuid
from pathlib import Path

from pydantic import Field

from optim.engine import Task, solve_all
from optim.jobs import Run
from optim.outputs.snapshots import load_run, save_run
from optim.schema import Strict
from optim.strategy import Strategy


class ReplayRequest(Strict):
    strategy: Strategy
    snapshot: Path  # the run's snapshot directory
    accounts: list[str] | None = Field(default=None, min_length=1)  # a subset of the run's accounts; None replays all of them


def replay(request: ReplayRequest, *, workers: int) -> Run:
    """Saved as a new run, beside the run it replays."""
    run_id = str(uuid.uuid7())
    market, portfolios = load_run(request.snapshot)
    if request.accounts is not None:
        saved = {p.account.account_id for p in portfolios}
        if missing := [a for a in request.accounts if a not in saved]:
            msg = f"{len(missing)} accounts are not in {request.snapshot}, e.g. {missing[:5]}"
            raise LookupError(msg)
        portfolios = [p for p in portfolios if p.account.account_id in request.accounts]
    results = solve_all(market, [Task(p, request.strategy) for p in portfolios], workers=workers)
    save_run(request.snapshot.parent, run_id, job="replay", request=request, market=market, portfolios=portfolios, results=results)
    return Run(run_id, results)
