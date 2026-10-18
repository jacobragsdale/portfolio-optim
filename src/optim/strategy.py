"""`Strategy`: the JSON contract that traders and agents write. `optim schema` prints its JSON schema."""

from typing import Literal, Self

from pydantic import Field, model_validator

from optim.optimizer.constraints import Constraint
from optim.optimizer.objective import Cost, ExpectedReturn, Tax, Term
from optim.rules import Rule
from optim.schema import StrictModel


class SolveSettings(StrictModel):
    """How to solve. An integer solve trades exactly the sizes the market trades. An LP solve is much faster, but its
    trades are rounded afterwards, which can break a binding constraint, and such a result makes the run exit nonzero."""

    integer: bool = True
    solver: Literal["HIGHS"] = "HIGHS"
    """HIGHS only. Another solver cvxpy supports, such as a licensed COPT, is added here once it is installed."""
    time_limit_s: float = Field(default=30.0, gt=0)
    """A solve that hits it keeps the best solution found, if any."""
    options: dict[str, float | int | bool | str] = Field(default_factory=dict)
    """Passed to the solver, named as its docs name them."""


def _expected_return_net_of_tax_and_cost() -> list[Term]:
    return [ExpectedReturn(), Tax(), Cost()]


class Strategy(StrictModel):
    """How to optimize: rules applied in order before the solve, the objective's terms, constraints, and how to solve."""

    name: str
    rules: list[Rule] = Field(default_factory=list)
    objective: list[Term] = Field(default_factory=_expected_return_net_of_tax_and_cost)
    constraints: list[Constraint] = Field(default_factory=list)
    solve: SolveSettings = SolveSettings()

    @model_validator(mode="after")
    def _integer_only_constraints(self) -> Self:
        """A constraint that counts trades needs the 0/1 flags only an integer solve has: refuse it here, not at every solve."""
        if not self.solve.integer and (counting := [c.kind for c in self.constraints if getattr(c, "integer_only", False)]):
            msg = f'{", ".join(counting)} counts trades, which needs "solve": {{"integer": true}}'
            raise ValueError(msg)
        return self
