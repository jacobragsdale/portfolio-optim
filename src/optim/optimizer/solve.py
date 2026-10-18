"""Solve one portfolio: build the model, solve it, make its trades exact, and re-check every constraint."""

import logging
import time
import warnings
from collections.abc import Mapping

import cvxpy as cp
import highspy
import numpy as np

from optim.inputs import SolverInputs
from optim.logs import ms_since
from optim.optimizer.model import build_model
from optim.optimizer.results import Result, Solved, Unsolved
from optim.optimizer.rounding import round_to_lots, snap_integers, violations
from optim.strategy import Strategy

log = logging.getLogger(__name__)
# cvxpy warns this on every time-limited solve; `solve` logs its own warning, with the portfolio and the gap
warnings.filterwarnings("ignore", message="Solution may be inaccurate", category=UserWarning)


def solve(inputs: SolverInputs, strategy: Strategy) -> Result:
    start = time.perf_counter()
    portfolio_id, how = inputs.portfolio.portfolio_id, strategy.solve
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
            "portfolio_id": portfolio_id,
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
        return Unsolved(portfolio_id, strategy.name, _why_unsolved(problem, named))
    if stopped_early:
        log.warning("%s hit the %gs time limit; keeping the best solution found", portfolio_id, how.time_limit_s, extra={"portfolio_id": portfolio_id, "strategy": strategy.name, "gap": gap})
    optimum = float(problem.value)
    if how.integer:
        snap_integers(problem)
    else:
        round_to_lots(m)
    return Solved.from_model(m, portfolio_id=portfolio_id, strategy=strategy.name, objective=optimum, gap=gap, violations=violations(named), seconds=time.perf_counter() - start)


def _why_unsolved(problem: cp.Problem, constraints: Mapping[str, list[cp.Constraint]]) -> str:
    """The solver's status. When infeasible, also what the portfolio breaks before any trade: usually the cause, as when
    a position already over its limit cannot be sold down within the turnover allowed."""
    if problem.status not in (cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE):
        return str(problem.status)
    for variable in problem.variables():
        if variable.size:  # cvxpy cannot set an empty one
            variable.value = np.zeros(variable.shape)
    broken = violations(constraints)
    return f"{problem.status}; before any trade it breaks " + ", ".join(f"{name} (by {excess:.3g})" for name, excess in broken.items()) if broken else str(problem.status)
