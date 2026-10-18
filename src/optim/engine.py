"""Solving tasks, in this process or a process pool.

Workers only compute. The parent loads the data, ships the `Market` once to each worker process, sends small tasks (one
portfolio and a strategy), and writes the outputs. Workers hold no connections, and moving from one machine to a
cluster (Ray, Dask) changes only `solve_all`.
"""

import logging
import multiprocessing
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import partial

from optim import logs
from optim.inputs import Inputs, Market, Portfolio
from optim.optimizer.results import Result, Solved, Unsolved
from optim.optimizer.solve import solve
from optim.rules import Rule
from optim.strategy import Strategy

log = logging.getLogger(__name__)

_PROCESSES = multiprocessing.get_context("forkserver")
_PROCESSES.set_forkserver_preload(["optim.engine"])  # new workers fork with numpy, pandas and cvxpy already imported
# Workers share the cores, so each gets a one-thread BLAS: 11% more portfolios a minute at 8 workers on 8 cores. The
# forkserver reads it as it starts, with the first pool; this process loaded its BLAS before.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


@dataclass(frozen=True)
class Task:
    portfolio: Portfolio
    strategy: Strategy


def solve_task(market: Market, task: Task) -> Result:
    """One portfolio, logged in whichever process solves it. A failure is logged here, where its traceback is whole."""
    portfolio_id, strategy = task.portfolio.details.portfolio_id, task.strategy.name
    try:
        inputs = market.inputs_for(task.portfolio)
        for rule in task.strategy.rules:
            inputs = _apply(rule, inputs)
        result = solve(inputs, task.strategy)
    except Exception as error:
        log.exception("solve failed", extra={"portfolio_id": portfolio_id, "strategy": strategy})
        return Unsolved(portfolio_id, strategy, f"error: {error!r}")
    match result:
        case Solved():
            log.info(
                "solved %s",
                portfolio_id,
                extra={"portfolio_id": portfolio_id, "strategy": strategy, "status": result.status, "seconds": result.seconds}
                | result.summary
                | {"trades": result.trades.drop(columns="portfolio_id")},
            )
        case Unsolved():
            log.warning("could not solve %s: %s", portfolio_id, result.status, extra={"portfolio_id": portfolio_id, "strategy": strategy})
    return result


def _apply(rule: Rule, inputs: Inputs) -> Inputs:
    after = rule.apply(inputs)
    was, now = inputs.securities, after.securities
    dropped = was.loc[was["buyable"] & ~now["buyable"], "cusip"]
    changed = int((was["expected_return"] != now["expected_return"]).sum())
    log.debug(
        "rule applied",
        extra={
            "portfolio_id": inputs.portfolio.portfolio_id,
            "rule": rule,
            "buyable": int(now["buyable"].sum()),
            "dropped": len(dropped),
            "dropped_examples": dropped.head(20).tolist(),
            "returns_changed": changed,
        },
    )
    return after


def solve_all(market: Market, tasks: Sequence[Task], *, workers: int) -> list[Result]:
    """Solve every task in a pool of up to `workers` processes; results come back in task order."""
    workers = min(workers, len(tasks))
    with logs.timed(log, "solved portfolios", level=logging.INFO, portfolios=len(tasks), workers=workers) as fields:
        # one worker, the common intraday case, solves in this process: no pool start-up cost
        results = _in_pools(market, tasks, workers) if workers > 1 else [_settle(task, partial(solve_task, market, task)) for task in tasks]
        not_ok = [r.portfolio_id for r in results if not (isinstance(r, Solved) and r.ok)]
        fields.update(not_ok=len(not_ok), not_ok_examples=not_ok[:20])
    return results


def _in_pools(market: Market, tasks: Sequence[Task], workers: int) -> list[Result]:
    """A worker that dies, as when the kernel kills one that ran out of memory, breaks its pool and loses every portfolio
    not yet solved. Those get one new pool, which covers a one-off kill; if that breaks too, they stay unsolved."""
    results = _in_pool(market, tasks, workers)
    if lost := [i for i, result in enumerate(results) if result is None]:
        log.warning("a solver process died, as when the kernel kills one that runs out of memory; solving its %d unfinished portfolios in a new pool", len(lost))
        for i, result in zip(lost, _in_pool(market, [tasks[i] for i in lost], workers), strict=True):
            results[i] = result
    if lost := [tasks[i] for i, result in enumerate(results) if result is None]:
        log.error(
            "a solver process died again, so %d portfolios stay unsolved; give the Job more memory or fewer OPTIM_WORKERS",
            len(lost),
            extra={"examples": [t.portfolio.details.portfolio_id for t in lost[:20]]},
        )
    return [result or Unsolved(task.portfolio.details.portfolio_id, task.strategy.name, "error: its solver process died") for task, result in zip(tasks, results, strict=True)]


def _in_pool(market: Market, tasks: Sequence[Task], workers: int) -> list[Result | None]:
    """Solve in a new pool; None for a portfolio lost when the pool broke."""
    # ponytail: one machine; for a cluster, ship `market` once (ray.put / client.scatter) and submit the same tasks,
    # each carrying `logs.bound()`, since a cluster's workers outlive a run
    with ProcessPoolExecutor(workers, mp_context=_PROCESSES, initializer=_start_worker, initargs=(market, logs.bound())) as pool:
        futures = [pool.submit(_solve_in_worker, task) for task in tasks]
        return [None if isinstance(future.exception(), BrokenProcessPool) else _settle(task, future.result) for task, future in zip(tasks, futures, strict=True)]


def _settle(task: Task, outcome: Callable[[], Result]) -> Result:
    """One portfolio's failure must not block the rest of the batch: it becomes an `Unsolved`, which makes the run exit
    nonzero. `solve_task` catches its own errors; what reaches here failed between processes, as
    when a task cannot be pickled."""
    portfolio_id = task.portfolio.details.portfolio_id
    try:
        return outcome()
    except Exception as error:
        log.exception("solve failed", extra={"portfolio_id": portfolio_id, "strategy": task.strategy.name})
        return Unsolved(portfolio_id, task.strategy.name, f"error: {error!r}")


_market: Market | None = None  # this worker process's copy, set once by the pool initializer


def _start_worker(market: Market, fields: dict[str, object]) -> None:
    global _market  # noqa: PLW0603 -- the ProcessPoolExecutor initializer pattern: one copy per worker, not per task
    _market = market
    logs.setup()  # a forkserver worker starts without the parent's logging; it reads the same OPTIM_LOG_* variables
    logs.bind(**fields)  # nor its bound fields, such as the run's id


def _solve_in_worker(task: Task) -> Result:
    if _market is None:
        msg = "worker started without a market"
        raise RuntimeError(msg)
    return solve_task(_market, task)
