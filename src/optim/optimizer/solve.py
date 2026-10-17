"""Solve one portfolio: build the model, solve it, make its trades exact, and re-check every constraint."""

import logging
import time

import cvxpy as cp
import highspy

from optim.inputs import Inputs
from optim.logs import ms_since
from optim.optimizer.model import build_model
from optim.optimizer.results import Result, Solved, Unsolved, solver_inputs
from optim.optimizer.rounding import round_to_lots, snap_integers, violations
from optim.strategy import Strategy

log = logging.getLogger(__name__)


def solve(inputs: Inputs, strategy: Strategy) -> Result:
    start = time.perf_counter()
    account_id, how = inputs.account.account_id, strategy.solve
    m = build_model(inputs, integer=how.integer)
    named = {"bounds": m.bounds} | {f"{i}:{c.kind}": c.build(m) for i, c in enumerate(strategy.constraints)}
    objective = sum((term.expression(m) for term in strategy.objective), start=cp.Constant(0.0))
    problem = cp.Problem(cp.Maximize(objective), [c for group in named.values() for c in group])
    built = ms_since(start)
    # ponytail: `time_limit` and the stats below are HiGHS's; map them when adding another solver
    problem.solve(solver=how.solver, warm_start=False, time_limit=how.time_limit_s, **how.options)  # warm starts made HiGHS LP re-solves ~6x slower
    size, stats, info = problem.size_metrics, problem.solver_stats, problem.solver_stats.extra_stats
    if not isinstance(info, highspy.HighsInfo):
        msg = f"expected HiGHS's solver stats, got {type(info).__name__}"
        raise TypeError(msg)
    gap = info.mip_gap if how.integer else None
    log.debug(
        "solver finished",
        extra={
            "account_id": account_id,
            "strategy": strategy.name,
            "status": problem.status,
            "solve": how,
            "objective": strategy.objective,
            "constraints": strategy.constraints,
            "securities": len(m.securities),
            "buyable": int(m.securities["buyable"].sum()),
            "lots": len(m.lots),
            "value": m.value,
            "variables": size.num_scalar_variables,
            "rows": size.num_scalar_eq_constr + size.num_scalar_leq_constr,
            "iterations": stats.num_iters,
            "gap": gap,
            "build_ms": built,
            "compile_ms": round((problem.compilation_time or 0) * 1000, 1),
            "solver_ms": round((stats.solve_time or 0) * 1000, 1),
        },
    )
    stopped_early = problem.status == cp.USER_LIMIT and info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible.value
    if not (problem.status == cp.OPTIMAL or stopped_early):
        return Unsolved(account_id, strategy.name, str(problem.status), solver_inputs=solver_inputs(m))
    if stopped_early:
        log.warning("%s hit the %gs time limit; keeping the best solution found", account_id, how.time_limit_s, extra={"account_id": account_id, "strategy": strategy.name, "gap": gap})
    optimum = float(problem.value)
    if how.integer:
        snap_integers(problem)
    else:
        round_to_lots(m)
    return Solved.from_model(m, account_id=account_id, strategy=strategy.name, objective=optimum, gap=gap, violations=violations(named), seconds=time.perf_counter() - start)
