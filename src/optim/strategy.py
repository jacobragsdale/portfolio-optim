"""`Strategy`: the JSON contract that traders and agents write. `optim schema` prints its JSON schema."""

from optim.optimizer.constraints import Constraint
from optim.optimizer.objective import Objective
from optim.rules import Rule
from optim.schema import Strict


class Strategy(Strict):
    """How to optimize: rules applied in order before the solve, the objective, and constraints."""

    name: str
    rules: tuple[Rule, ...] = ()
    objective: Objective = Objective()
    constraints: tuple[Constraint, ...] = ()
