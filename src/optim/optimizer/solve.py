"""Solve one portfolio: build the model, solve the LP with HiGHS, round to tradable lots, and re-check."""

import time
from collections.abc import Sequence

import cvxpy as cp

from optim.inputs import Inputs
from optim.optimizer.constraints import Constraint
from optim.optimizer.model import build_model
from optim.optimizer.objective import Objective
from optim.optimizer.results import Result, Solved, Unsolved
from optim.optimizer.rounding import round_to_lots, violations


def solve(inputs: Inputs, objective: Objective, constraints: Sequence[Constraint], *, strategy: str) -> Result:
    start = time.perf_counter()
    account_id = inputs.account.account_id
    m = build_model(inputs)
    named = {"bounds": m.bounds} | {f"{i}:{c.kind}": c.build(m) for i, c in enumerate(constraints)}
    problem = cp.Problem(cp.Maximize(objective.expression(m)), [c for group in named.values() for c in group])
    problem.solve(solver=cp.HIGHS, warm_start=False)  # warm starts made HiGHS re-solves ~6x slower when measured
    if problem.status != cp.OPTIMAL:
        return Unsolved(account_id, strategy, str(problem.status))
    lp_optimum = float(problem.value)
    round_to_lots(m)
    return Solved.from_model(m, account_id=account_id, strategy=strategy, objective=lp_optimum, violations=violations(named), seconds=time.perf_counter() - start)
