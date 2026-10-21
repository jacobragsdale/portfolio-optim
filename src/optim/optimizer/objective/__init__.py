"""What the optimizer maximizes: the sum of the strategy's objective terms. Each term returns what it adds to the
objective as a fraction of base value, so terms share one scale; a penalty returns a negative amount.

A quadratic term sets `quadratic`: HiGHS solves it only without integers, so a strategy that pairs one with an integer
HiGHS solve is refused when it is read (`Strategy`), never solved with a linear stand-in.

Terms are grouped by what they reward or penalize, a module each. `Term` is the union that strategy JSON is validated
against, so every term class must be added to it.
"""

from typing import Annotated

from pydantic import Field

from optim.optimizer.objective.ladder import LadderBalance
from optim.optimizer.objective.returns import ExpectedReturn
from optim.optimizer.objective.tracking import ExposureTracking, TrackingL1
from optim.optimizer.objective.trading import Cost, TurnoverQuadratic

# TODO(port): the old system's objective, its penalties and preferences, are term classes, in a module here per subject,
# added to this union.
Term = Annotated[ExpectedReturn | Cost | TrackingL1 | TurnoverQuadratic | ExposureTracking | LadderBalance, Field(discriminator="kind")]
