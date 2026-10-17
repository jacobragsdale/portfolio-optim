"""Constraints, grouped by what they limit. Each is a pydantic class whose fields are its JSON parameters and whose
`build` returns cvxpy constraints written in weights against the `Model`.

`Constraint` is the union that strategy JSON is validated against, so every constraint class must be added to it.
"""

from typing import Annotated

from pydantic import Field

from optim.optimizer.constraints.limits import GroupLimit, PositionLimit
from optim.optimizer.constraints.risk import DurationBand
from optim.optimizer.constraints.tax import MaxTax
from optim.optimizer.constraints.trading import CashRange, MaxTurnover

Constraint = Annotated[PositionLimit | GroupLimit | DurationBand | MaxTurnover | CashRange | MaxTax, Field(discriminator="kind")]
