"""Solving tasks, in this process or a process pool.

Workers only compute. The parent loads the data, ships the `Market` once to each worker process, sends small tasks (one
portfolio and a strategy), and writes the outputs. Workers hold no connections, and moving from one machine to a
cluster (Ray, Dask) changes only `solve_all`.
"""

import logging
import multiprocessing
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
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


@dataclass(frozen=True)
class Task:
    portfolio: Portfolio
    strategy: Strategy


def solve_task(market: Market, task: Task) -> Result:
    """One portfolio, logged in whichever process solves it. A failure is logged here, where its traceback is whole."""
    account_id, strategy = task.portfolio.account.account_id, task.strategy.name
    try:
        inputs = market.inputs_for(task.portfolio)
        for rule in task.strategy.rules:
            inputs = _apply(rule, inputs)
        result = solve(inputs, task.strategy.objective, task.strategy.constraints, strategy=strategy)
    except Exception as error:
        log.exception("solve failed", extra={"account_id": account_id, "strategy": strategy})
        return Unsolved(account_id, strategy, f"error: {error!r}")
    match result:
        case Solved():
            log.info(
                "solved %s",
                account_id,
                extra={"account_id": account_id, "strategy": strategy, "status": result.status, "seconds": result.seconds} | result.summary | {"trades": result.trades.drop(columns="account_id")},
            )
        case Unsolved():
            log.warning("could not solve %s: %s", account_id, result.status, extra={"account_id": account_id, "strategy": strategy})
    return result


def _apply(rule: Rule, inputs: Inputs) -> Inputs:
    after = rule.apply(inputs)
    was, now = inputs.securities, after.securities
    dropped = was.loc[was["buyable"] & ~now["buyable"], "cusip"]
    changed = int((was["expected_return"] != now["expected_return"]).sum())
    log.debug(
        "rule applied",
        extra={
            "account_id": inputs.account.account_id,
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
        if workers <= 1:  # the common intraday case: no pool start-up cost
            results = [_settle(task, partial(solve_task, market, task)) for task in tasks]
        else:
            # ponytail: one machine; for a cluster, ship `market` once (ray.put / client.scatter) and submit the same tasks
            with ProcessPoolExecutor(workers, mp_context=_PROCESSES, initializer=_start_worker, initargs=(market,)) as pool:
                futures = [pool.submit(_solve_in_worker, task) for task in tasks]
                results = [_settle(task, future.result) for task, future in zip(tasks, futures, strict=True)]
        fields["not_ok"] = [r.account_id for r in results if not (isinstance(r, Solved) and r.ok)]
    return results


def _settle(task: Task, outcome: Callable[[], Result]) -> Result:
    """One portfolio's failure must not block the rest of the batch: it becomes an `Unsolved`, kept out of orders, that
    makes the run exit nonzero. `solve_task` catches its own errors; what reaches here broke the pool, as when the
    kernel kills a worker that ran out of memory."""
    account_id = task.portfolio.account.account_id
    try:
        return outcome()
    except Exception as error:
        log.exception("solve failed", extra={"account_id": account_id, "strategy": task.strategy.name})
        return Unsolved(account_id, task.strategy.name, f"error: {error!r}")


_market: Market | None = None  # this worker process's copy, set once by the pool initializer


def _start_worker(market: Market) -> None:
    global _market  # noqa: PLW0603 -- the ProcessPoolExecutor initializer pattern: one copy per worker, not per task
    _market = market
    logs.setup()  # a forkserver worker starts without the parent's logging; it reads the same OPTIM_LOG_* variables


def _solve_in_worker(task: Task) -> Result:
    if _market is None:
        msg = "worker started without a market"
        raise RuntimeError(msg)
    return solve_task(_market, task)
