"""`Strategy`: the JSON contract that traders and agents write. `optim schema` prints its JSON schema."""

from typing import Literal

from pydantic import Field

from optim.optimizer.constraints import Constraint
from optim.optimizer.objective import Cost, ExpectedReturn, Tax, Term
from optim.rules import Rule
from optim.schema import Strict


class Solve(Strict):
    """How to solve. An integer solve trades exactly the sizes the market trades. An LP solve is much faster, but its
    trades are rounded afterwards, which can break a binding constraint, and such a result is never ordered."""

    integer: bool = True
    solver: Literal["HIGHS"] = "HIGHS"  # any solver cvxpy supports, such as a licensed COPT, once it is installed
    time_limit_s: float = Field(default=30.0, gt=0)  # a solve that hits it keeps the best solution found, if any
    options: dict[str, float | int | bool | str] = Field(default_factory=dict)  # passed to the solver, named as its docs name them


def _expected_return_net_of_tax_and_cost() -> list[Term]:
    return [ExpectedReturn(), Tax(), Cost()]


class Strategy(Strict):
    """How to optimize: rules applied in order before the solve, the objective's terms, constraints, and how to solve."""

    name: str
    rules: list[Rule] = Field(default_factory=list)
    objective: list[Term] = Field(default_factory=_expected_return_net_of_tax_and_cost)
    constraints: list[Constraint] = Field(default_factory=list)
    solve: Solve = Solve()
