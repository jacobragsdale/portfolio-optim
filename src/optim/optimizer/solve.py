"""Solve one portfolio: build the model, solve it with the strategy's solver, make its trades exact, and review them for
acceptance."""

import logging
import time
import warnings
from collections.abc import Mapping

import cvxpy as cp
import numpy as np

from optim.logs import ms_since
from optim.optimizer.acceptance import review, violations
from optim.optimizer.backends import SOLVERS, Termination
from optim.optimizer.model import OptimizationModel, build_model, value_of
from optim.optimizer.results import Result, Unsolved
from optim.optimizer.rounding import round_to_lots, snap_integers
from optim.prep import SolverInputs
from optim.strategy import Strategy

log = logging.getLogger(__name__)
# cvxpy warns this on every time-limited solve; `solve` logs its own warning, with the portfolio and the gap
warnings.filterwarnings("ignore", message="Solution may be inaccurate", category=UserWarning)


def solve(inputs: SolverInputs, strategy: Strategy) -> Result:
    start = time.perf_counter()
    portfolio_id, how = inputs.portfolio.details.portfolio_id, strategy.solve
    backend = SOLVERS[how.solver]
    m = build_model(inputs, integer=how.integer)
    constraints = {f"{i}:{c.kind}": c.build(m) for i, c in enumerate(strategy.constraints)}
    named = {"bounds": m.bounds} | constraints
    objective = sum((term.expression(m) for term in strategy.objective), start=cp.Constant(0.0))
    problem = cp.Problem(cp.Maximize(objective), [c for group in named.values() for c in group])
    built = ms_since(start)
    if problem.size_metrics.num_scalar_variables == 0:  # nothing can be bought or sold, as in a frozen portfolio: solvers refuse an empty model
        return _nothing_to_trade(m, problem, constraints, strategy, start)
    problem.solve(solver=how.solver, warm_start=False, **backend.options(how.time_limit_s, how.options))  # warm starts made HiGHS LP re-solves ~6x slower
    done, size, stats = backend.termination(problem, integer=how.integer), problem.size_metrics, problem.solver_stats
    log.info(
        "solver finished",
        extra={
            "portfolio_id": portfolio_id,
            "strategy": strategy.name,
            "stop": done.stop,
            "solver_status": done.solver_status,
            "solve": how,
            "objective": strategy.objective,
            "constraints": strategy.constraints,
            "lines": len(m.lines),
            "buyable": int(m.lines["buyable"].sum()),
            "sellable": int(m.lines["sellable"].sum()),
            "value": m.value,
            "base": m.base,
            "variables": size.num_scalar_variables,
            "rows": size.num_scalar_eq_constr + size.num_scalar_leq_constr,
            "iterations": stats.num_iters,
            "gap": done.gap,
            "nodes": done.nodes,
            "build_ms": built,
            "compile_ms": round((problem.compilation_time or 0) * 1000, 1),
            "solver_ms": round((stats.solve_time or 0) * 1000, 1),
        },
    )
    if not done.has_solution:
        return Unsolved(portfolio_id, strategy.name, _why_unsolved(done, problem, named))
    if done.stop == "limit":
        log.warning("%s hit the %gs time limit; reviewing the best solution found", portfolio_id, how.time_limit_s, extra={"portfolio_id": portfolio_id, "strategy": strategy.name, "gap": done.gap})
    optimum = float(problem.value)
    if how.integer:
        snap_integers(problem)
    else:
        round_to_lots(m)
    return review(m, constraints, done, strategy=strategy.name, objective=optimum, max_gap=how.max_gap, start=start)


def _nothing_to_trade(m: OptimizationModel, problem: cp.Problem, constraints: Mapping[str, list[cp.Constraint]], strategy: Strategy, start: float) -> Result:
    """No trades, reviewed as any result is: a portfolio that cannot trade may still break a limit, or be short of cash."""
    for variable in problem.variables():
        variable.save_value(np.zeros(variable.shape))  # each is empty, which `.value =` refuses for an integer variable
    done = Termination("optimal", "nothing to trade: no line can be bought or sold", has_solution=True, gap=None, nodes=None)
    log.info("nothing to trade", extra={"portfolio_id": m.inputs.portfolio.details.portfolio_id, "strategy": strategy.name, "lines": len(m.lines)})
    return review(m, constraints, done, strategy=strategy.name, objective=float(value_of(problem.objective.expr)), max_gap=strategy.solve.max_gap, start=start)


def _why_unsolved(done: Termination, problem: cp.Problem, constraints: Mapping[str, list[cp.Constraint]]) -> str:
    """How the solver stopped. When infeasible, also what the portfolio breaks before any trade: usually the cause, as
    when a position already over its limit cannot be sold down within the turnover allowed, or when a mandatory sale is
    what breaks it."""
    if done.stop != "infeasible":
        return f"{done.stop} with no solution ({done.solver_status})"
    for variable in problem.variables():
        if variable.size:  # cvxpy cannot set an empty one
            variable.value = np.zeros(variable.shape)
    broken = violations(constraints)
    return "infeasible; before any trade it breaks " + ", ".join(f"{name} (by {excess:.3g})" for name, excess in broken.items()) if broken else "infeasible"
