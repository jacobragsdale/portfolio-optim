"""Entry points. A job is its request, one module each: a pydantic model of what was asked, the same whether the CLI or
an API built it, with `portfolio_ids`, the portfolios it loads, and `tasks`, the solves it makes from the loaded
datasets. `run` loads, prepares, solves and saves any job. The request is saved in the snapshot, so every run records
what was asked, and `replay` runs it again from there: every job replays the same way. A new job is a request class added
to `Job`, and a CLI subcommand."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import ConfigDict, Field, TypeAdapter

from optim import logs
from optim.context import Context
from optim.engine import SolveTask, solve_all
from optim.inputs import Frames, build_market, load_frames
from optim.jobs.model_portfolio import ModelPortfolioRequest
from optim.jobs.optimize import OptimizeRequest
from optim.jobs.transition import TransitionRequest
from optim.optimizer.results import Result, Solved
from optim.outputs.snapshots import load_run, save_run
from optim.schema import Model
from optim.strategy import Strategy

Job = Annotated[OptimizeRequest | ModelPortfolioRequest | TransitionRequest, Field(discriminator="job")]
JOB: TypeAdapter[Job] = TypeAdapter(Job)


@dataclass(frozen=True)
class Run:
    run_id: str  # names the run's snapshot directory
    results: list[Result]

    @property
    def ok(self) -> bool:
        """Every portfolio solved, and rounding broke nothing."""
        return all(isinstance(r, Solved) and r.ok for r in self.results)


def run(ctx: Context, job: Job) -> Run:
    """Load what the job reads, then prepare, solve and save it."""
    run_id = ctx.new_run_id()
    logs.bind(run_id=run_id)
    frames = load_frames(ctx.loader, job.portfolio_ids, job.as_of_date)
    return _solve(ctx.snapshot_dir, run_id, job, frames, job.tasks(frames), workers=ctx.workers)


class ReplayRequest(Model):
    snapshot: Path  # the run's snapshot directory
    strategy: Strategy | None = None  # None: the strategy the run used
    portfolios: list[str] | None = Field(default=None, min_length=1)  # a subset of the run's portfolios; None: those the run solved


class Replayed(Model):
    of: str  # the run a replay replayed
    portfolios: list[str] | None  # the subset it solved; None: all of them


class Saved(Model):
    """What replay reads of a snapshot's `run.json`."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    request: Job
    replay: Replayed | None = None


def replay(request: ReplayRequest, *, workers: int) -> Run:
    """Run a saved job again on the datasets its snapshot holds, with the code you have now and, if given, another
    strategy. It reads only the snapshot: no settings, database or cache. Saved as a new run of the same job, beside the
    run it replays, so a replay can be replayed too."""
    run_id = str(uuid.uuid7())  # replays touch no shared system, so they are not registered in the runs table
    logs.bind(run_id=run_id)
    meta, frames = load_run(request.snapshot)
    saved = Saved.model_validate(meta)
    job = saved.request if request.strategy is None else JOB.validate_python(saved.request.model_dump() | {"strategy": request.strategy})
    tasks = job.tasks(frames)
    only = request.portfolios or (saved.replay.portfolios if saved.replay else None)  # a replay of a subset replays that subset
    if only is not None:
        tasks = _only(tasks, only, request.snapshot)
    return _solve(request.snapshot.parent, run_id, job, frames, tasks, workers=workers, replay=Replayed(of=saved.run_id, portfolios=only))


def _solve(root: Path, run_id: str, job: Job, frames: Frames, tasks: Sequence[SolveTask], *, workers: int, **extra: object) -> Run:
    results = solve_all(build_market(frames, job.as_of_date), tasks, workers=workers)
    save_run(root, run_id, job=job.job, as_of_date=job.as_of_date, request=job, frames=frames, results=results, **extra)
    return Run(run_id, results)


def _only(tasks: Sequence[SolveTask], portfolio_ids: Sequence[str], snapshot: Path) -> list[SolveTask]:
    in_run = {task.portfolio.details.portfolio_id for task in tasks}
    if missing := [i for i in portfolio_ids if i not in in_run]:
        msg = f"{len(missing)} portfolios are not in {snapshot}, e.g. {missing[:5]}"
        raise LookupError(msg)
    return [task for task in tasks if task.portfolio.details.portfolio_id in portfolio_ids]
