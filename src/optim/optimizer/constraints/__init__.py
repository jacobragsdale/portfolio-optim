"""Constraints, grouped by what they limit. Each is a pydantic class whose fields are its JSON parameters and whose
`build` returns cvxpy constraints written in weights against the `OptimizationModel`, for the whole portfolio or one
sleeve (`Scoped`).

`Constraint` is the union that strategy JSON is validated against, so every constraint class must be added to it. One
that counts trades sets `integer_only`, and a strategy that pairs it with an LP solve is refused when it is read.
"""

from typing import Annotated

from pydantic import Field

from optim.optimizer.constraints.exposures import ExposureLimit
from optim.optimizer.constraints.limits import GroupLimit, PositionLimit
from optim.optimizer.constraints.trading import MaxTrades, MaxTurnover

# TODO(port): the old system's hard limits are constraint classes, in a module here per subject, added to this union.
Constraint = Annotated[PositionLimit | GroupLimit | ExposureLimit | MaxTurnover | MaxTrades, Field(discriminator="kind")]
