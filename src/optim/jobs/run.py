"""Running a job: `run` loads, prepares, solves and saves it. The request is saved in the snapshot, so every run records
what was asked, and `replay` runs it again from there: every job replays the same way. `RunInfo` is the snapshot's
`run.json`."""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType

from pydantic import AwareDatetime, Field

from optim import logs
from optim.context import Context
from optim.data.loader import DatasetRead
from optim.datasets.load import load_frames
from optim.engine import SolveTask, solve_all
from optim.jobs import JOB, Job
from optim.optimizer.results import Result, Solved
from optim.outputs.snapshots import Code, load_run, save_run
from optim.prep import Frames
from optim.prep.market import build_market
from optim.schema import Model, RunId
from optim.strategy import Strategy

NO_LABELS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class Run:
    run_id: str  # names the run's snapshot directory
    results: list[Result]

    @property
    def ok(self) -> bool:
        """Every portfolio solved, and every result passed acceptance: the run may trade."""
        return all(isinstance(r, Solved) and r.accepted for r in self.results)


class Replayed(Model):
    of: str
    """The run a replay replayed."""
    portfolios: list[str] | None
    """The subset it solved; null for every portfolio of that run."""


class RunInfo(Model):
    """A snapshot's `run.json`: what the run was asked, when it ran, with which code, and where its data came from.
    It holds nothing the snapshot's other files hold. [Snapshot files](docs/snapshots.md) describes each key."""

    run_id: RunId
    job: str
    """The request's job. A replay keeps the job of the run it replays."""
    as_of_date: date
    started_at: AwareDatetime
    """When the run started, before it loaded anything."""
    loaded_at: AwareDatetime
    """When every dataset had loaded; for a replay, when its snapshot had been read and its portfolios prepared."""
    finished_at: AwareDatetime
    """When the last solve finished, just before the snapshot was written."""
    code: Code
    labels: dict[str, str]
    """Set by whoever started the run (`--label key=value`), such as `purpose=production` or Airflow's DAG run."""
    workers: int
    """Solver processes. A solve that stops at its time limit depends on how many solves shared the machine."""
    datasets: dict[str, DatasetRead] | None
    """How each dataset was read, and when its source was. Null for a replay, whose data is the run's it replays."""
    request: Job
    replay: Replayed | None
    """A replay only: the run it replayed, and the portfolios it solved."""


def run(ctx: Context, job: Job, *, labels: Mapping[str, str] = NO_LABELS) -> Run:
    """Load what the job reads, then prepare, solve and save it, with `labels` in its `run.json`. The run's id is a new
    UUIDv7, so ids sort by when their runs started."""
    run_id = str(uuid.uuid7())
    logs.bind(run_id=run_id)
    started_at = ctx.clock()
    frames, reads = load_frames(ctx.loader, job.portfolio_ids, job.as_of_date, job.holdings)
    # TODO(port): the old system's outputs, such as reports or rows written to a database, are modules in outputs/,
    # called here on the run this returns: after the snapshot is saved, so a failed output never loses it, and never on
    # replay.
    return _solve_and_save(
        ctx.snapshot_dir, run_id, job, frames, job.tasks(frames), started_at=started_at, loaded_at=ctx.clock(), clock=ctx.clock, workers=ctx.workers, labels=labels, datasets=reads, replay=None
    )


class ReplayRequest(Model):
    snapshot: Path  # the run's snapshot directory
    strategy: Strategy | None = None  # None: the strategy the run used
    portfolios: list[str] | None = Field(default=None, min_length=1)  # a subset of the run's portfolios; None: those the run solved


def replay(request: ReplayRequest, *, workers: int, labels: Mapping[str, str] = NO_LABELS, clock: Callable[[], datetime] = logs.utc_now) -> Run:
    """Run a saved job again on the datasets its snapshot holds, with the code you have now and, if given, another
    strategy. Saved as a new run of the same job, beside the run it replays, so a replay can be replayed too."""
    run_id = str(uuid.uuid7())
    logs.bind(run_id=run_id)
    started_at = clock()
    loaded, frames = load_run(request.snapshot)
    saved = RunInfo.model_validate(loaded)
    job = saved.request if request.strategy is None else JOB.validate_python(saved.request.model_dump() | {"strategy": request.strategy})
    tasks = job.tasks(frames)
    only = request.portfolios or (saved.replay.portfolios if saved.replay else None)  # a replay of a subset replays that subset
    if only is not None:
        tasks = _only(tasks, only, request.snapshot)
    replayed = Replayed(of=saved.run_id, portfolios=only)
    return _solve_and_save(request.snapshot.parent, run_id, job, frames, tasks, started_at=started_at, loaded_at=clock(), clock=clock, workers=workers, labels=labels, datasets=None, replay=replayed)


def _solve_and_save(
    snapshot_dir: Path,
    run_id: str,
    job: Job,
    frames: Frames,
    tasks: Sequence[SolveTask],
    *,
    started_at: datetime,
    loaded_at: datetime,
    clock: Callable[[], datetime],
    workers: int,
    labels: Mapping[str, str],
    datasets: dict[str, DatasetRead] | None,
    replay: Replayed | None,
) -> Run:
    """Solve the tasks, then save the run's snapshot with its `run.json`."""
    results = solve_all(build_market(frames, job.as_of_date), tasks, workers=workers)
    meta = RunInfo(
        run_id=run_id,
        job=job.job,
        as_of_date=job.as_of_date,
        started_at=started_at,
        loaded_at=loaded_at,
        finished_at=clock(),
        code=Code.current(),
        labels=dict(labels),
        workers=workers,
        datasets=datasets,
        request=job,
        replay=replay,
    )
    save_run(snapshot_dir, run_id, meta=meta, frames=frames, results=results)
    return Run(run_id, results)


def _only(tasks: Sequence[SolveTask], portfolio_ids: Sequence[str], snapshot: Path) -> list[SolveTask]:
    in_run = {task.portfolio.details.portfolio_id for task in tasks}
    if missing := [i for i in portfolio_ids if i not in in_run]:
        msg = f"{len(missing)} portfolios are not in {snapshot}, e.g. {missing[:5]}"
        raise LookupError(msg)
    return [task for task in tasks if task.portfolio.details.portfolio_id in portfolio_ids]
