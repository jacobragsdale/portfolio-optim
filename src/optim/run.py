"""Solving: a `Strategy` (the JSON contract that traders and agents write) applied to portfolios, in this process or a
process pool.

Workers only compute. The parent loads the data, ships the `Market` once to each worker process, sends small tasks (one
portfolio and a strategy), and writes the outputs. Workers hold no database connections, and moving from one machine to
a cluster (Ray, Dask) changes only `solve_all`.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial

from optim import lp
from optim.data import Strict
from optim.inputs import Market, Portfolio
from optim.lp import Constraint, Objective, Result, Unsolved
from optim.rules import Rule

log = logging.getLogger(__name__)


class Strategy(Strict):
    """How to optimize: rules applied in order before the solve, the objective, and constraints. `optim schema` prints
    the JSON schema."""

    name: str
    rules: tuple[Rule, ...] = ()
    objective: Objective = Objective()
    constraints: tuple[Constraint, ...] = ()


@dataclass(frozen=True)
class Task:
    portfolio: Portfolio
    strategy: Strategy


def solve_task(market: Market, task: Task) -> Result:
    inputs = market.inputs_for(task.portfolio)
    for rule in task.strategy.rules:
        inputs = rule.apply(inputs)
    return lp.solve(inputs, task.strategy.objective, task.strategy.constraints, strategy=task.strategy.name)


def solve_all(market: Market, tasks: Sequence[Task], *, workers: int) -> list[Result]:
    """Solve every task; results come back in task order."""
    if workers <= 1 or len(tasks) <= 1:  # the common intraday case: no pool start-up cost
        return [_settle(task, partial(solve_task, market, task)) for task in tasks]
    # ponytail: one machine; for a cluster, ship `market` once (ray.put / client.scatter) and submit the same tasks
    with ProcessPoolExecutor(min(workers, len(tasks)), initializer=_set_market, initargs=(market,)) as pool:
        futures = [pool.submit(_solve_in_worker, task) for task in tasks]
        return [_settle(task, future.result) for task, future in zip(tasks, futures, strict=True)]


def _settle(task: Task, outcome: Callable[[], Result]) -> Result:
    """One portfolio's failure must not block the rest of the batch: it becomes an `Unsolved` that is logged here,
    kept out of orders, and makes the run exit nonzero."""
    account_id = task.portfolio.account.account_id
    try:
        return outcome()
    except Exception as error:
        log.exception("solve failed", extra={"account_id": account_id, "strategy": task.strategy.name})
        return Unsolved(account_id, task.strategy.name, f"error: {error!r}")


_market: Market | None = None  # this worker process's copy, set once by the pool initializer


def _set_market(market: Market) -> None:
    global _market  # noqa: PLW0603 -- the ProcessPoolExecutor initializer pattern: one copy per worker, not per task
    _market = market


def _solve_in_worker(task: Task) -> Result:
    if _market is None:
        msg = "worker started without a market"
        raise RuntimeError(msg)
    return solve_task(_market, task)
