"""`Strategy`: the JSON contract that traders and agents write. `optim schema` prints its JSON schema."""

from typing import Self

from pydantic import Field, model_validator

from optim.optimizer.backends import SOLVERS, SolverName
from optim.optimizer.constraints import Constraint
from optim.optimizer.objective import Cost, ExpectedReturn, Term
from optim.rules import Rule
from optim.schema import StrictModel


class SolveSettings(StrictModel):
    """How to solve, and when a solve's answer may trade. An integer solve trades exactly the sizes the market trades. An
    LP solve is much faster, but its trades are rounded afterwards, which can break a binding constraint, and such a
    result is rejected."""

    integer: bool = True
    solver: SolverName = "HIGHS"
    """`HIGHS`, free, or `COPT`, licensed (`uv sync --extra copt`). HiGHS cannot solve a quadratic objective with integer
    trades; COPT can."""
    time_limit_s: float = Field(default=30.0, gt=0)
    """A solve that hits it keeps the best solution found, if any, for acceptance to judge by `max_gap`."""
    max_gap: float = Field(default=0.01, ge=0)
    """Acceptance: an integer answer may trade only if it is within this of the best possible, relative, as the solver
    reports its gap, whether the solver stopped at its time limit or called it optimal under a looser gap option. An LP
    answer may trade when proved optimal."""
    options: dict[str, float | int | bool | str] = Field(default_factory=dict)
    """Passed to the solver, named as its docs name them, such as HiGHS's `mip_rel_gap` or COPT's `RelGap`. They override
    the engine's defaults for the solver, such as COPT's one thread."""


def _expected_return_net_of_cost() -> list[Term]:
    return [ExpectedReturn(), Cost()]


class Strategy(StrictModel):
    """How to optimize: rules applied in order before the solve, the objective's terms, constraints, and how to solve."""

    name: str
    rules: list[Rule] = Field(default_factory=list)
    objective: list[Term] = Field(default_factory=_expected_return_net_of_cost)
    constraints: list[Constraint] = Field(default_factory=list)
    solve: SolveSettings = SolveSettings()

    @model_validator(mode="after")
    def _solvable(self) -> Self:
        """Refuse here, not at every solve, what the solve settings cannot solve as written. Nothing is swapped to make it
        solvable: not the solver, nor a quadratic term for a linear one."""
        if not self.solve.integer and (counting := [c.kind for c in self.constraints if getattr(c, "integer_only", False)]):
            msg = f'{", ".join(counting)} counts trades, which needs "solve": {{"integer": true}}'
            raise ValueError(msg)
        quadratic = [t.kind for t in self.objective if getattr(t, "quadratic", False)]
        if quadratic and self.solve.integer and not SOLVERS[self.solve.solver].mixed_integer_quadratic:
            msg = f'{self.solve.solver} cannot solve a quadratic objective ({", ".join(quadratic)}) with integer trades: use "solver": "COPT", or "integer": false'
            raise ValueError(msg)
        return self
