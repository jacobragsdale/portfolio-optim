"""Solving one portfolio: build its problem from its constraints and objective, add the model's own constraints on its
trades, and solve it with the params' solve settings, through `optim.cvxpy`, which reads the answer as a decision."""

import cvxpy as cp
import optim
import optim.cvxpy

from optim.example import model
from optim.example.optimizer import constraints, problem


def solve(portfolio: model.Portfolio, params: model.Params) -> optim.Decision:
    """The portfolio's decision, with each objective term reported as a metric. A portfolio with nothing it may hold or
    buy has nothing to decide."""
    universe, value = portfolio.universe, portfolio.value
    if universe.empty:
        return optim.Decision()
    built = problem.build(universe, portfolio.constraints, portfolio.objective, value=value)
    own = [*constraints.sizes(universe, built.buy, built.sell, value=value), *constraints.cash(portfolio, built.buy, built.sell)]
    full = cp.Problem(built.problem.objective, [*built.problem.constraints, *own])
    return optim.cvxpy.solve(full, params.solve, securities=universe["security_id"].tolist(), buy=built.buy, sell=built.sell, metrics=built.terms)
