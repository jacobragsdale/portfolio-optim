"""Pre-solve rules. Each is a pure function of `SolverInputs`: it narrows what may be bought (`SolverInputs.restrict`) or adjusts
what the solver believes (`expected_return`). Rules do no I/O; a rule that needs more data gets a dataset and a column.

`Rule` is the union that strategy JSON is validated against, so every rule class must be added to it.
"""

from typing import Annotated

from pydantic import Field

from optim.rules.eligibility import ExcludeIssuers, MaturityRange, MinRating, TopSignals
from optim.rules.tax import AfterTax, NoWashSales

Rule = Annotated[MinRating | ExcludeIssuers | MaturityRange | TopSignals | AfterTax | NoWashSales, Field(discriminator="kind")]
