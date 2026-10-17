"""`Strategy`: the JSON contract that traders and agents write. `optim schema` prints its JSON schema."""

from pydantic import Field

from optim.optimizer.constraints import Constraint
from optim.optimizer.objective import Objective
from optim.rules import Rule
from optim.schema import Strict


class Strategy(Strict):
    """How to optimize: rules applied in order before the solve, the objective, and constraints."""

    name: str
    rules: list[Rule] = Field(default_factory=list)
    objective: Objective = Objective()
    constraints: list[Constraint] = Field(default_factory=list)
