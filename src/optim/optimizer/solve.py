"""Solve one portfolio: build the model, solve the LP with HiGHS, round to tradable lots, and re-check."""

import logging
import time
from collections.abc import Sequence

import cvxpy as cp

from optim.inputs import Inputs
from optim.logs import ms_since
from optim.optimizer.constraints import Constraint
from optim.optimizer.model import build_model
from optim.optimizer.objective import Objective
from optim.optimizer.results import Result, Solved, Unsolved, solver_inputs
from optim.optimizer.rounding import round_to_lots, violations

log = logging.getLogger(__name__)


def solve(inputs: Inputs, objective: Objective, constraints: Sequence[Constraint], *, strategy: str) -> Result:
    start = time.perf_counter()
    account_id = inputs.account.account_id
    m = build_model(inputs)
    named = {"bounds": m.bounds} | {f"{i}:{c.kind}": c.build(m) for i, c in enumerate(constraints)}
    problem = cp.Problem(cp.Maximize(objective.expression(m)), [c for group in named.values() for c in group])
    built = ms_since(start)
    problem.solve(solver=cp.HIGHS, warm_start=False)  # warm starts made HiGHS re-solves ~6x slower when measured
    size, stats = problem.size_metrics, problem.solver_stats
    log.debug(
        "solved lp",
        extra={
            "account_id": account_id,
            "strategy": strategy,
            "status": problem.status,
            "objective": objective,
            "constraints": constraints,
            "securities": len(m.securities),
            "buyable": int(m.securities["buyable"].sum()),
            "lots": len(m.lots),
            "value": m.value,
            "variables": size.num_scalar_variables,
            "rows": size.num_scalar_eq_constr + size.num_scalar_leq_constr,
            "iterations": stats.num_iters,
            "build_ms": built,
            "compile_ms": round((problem.compilation_time or 0) * 1000, 1),
            "solver_ms": round((stats.solve_time or 0) * 1000, 1),
        },
    )
    if problem.status != cp.OPTIMAL:
        return Unsolved(account_id, strategy, str(problem.status), solver_inputs=solver_inputs(m))
    lp_optimum = float(problem.value)
    round_to_lots(m)
    return Solved.from_model(m, account_id=account_id, strategy=strategy, objective=lp_optimum, violations=violations(named), seconds=time.perf_counter() - start)
