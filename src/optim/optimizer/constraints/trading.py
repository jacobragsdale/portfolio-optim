"""How much a scope trades: in value, and in number of trades."""

from typing import ClassVar, Literal

import cvxpy as cp
from pydantic import Field

from optim.optimizer.model import OptimizationModel, Scoped
from optim.schema import Weight


class MaxTurnover(Scoped):
    """Trade at most `max_weight` of the scope's base value, bought plus sold."""

    kind: Literal["max_turnover"] = "max_turnover"
    max_weight: Weight

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        scope = m.scope(self.sleeve)
        return [(scope.members * m.unit_value / scope.base) @ (m.buy + m.sell) <= self.max_weight]


class MaxTrades(Scoped):
    """At most `n` trades in the scope, buys and sales. Counts the integer solve's 0/1 flags, so it needs one."""

    kind: Literal["max_trades"] = "max_trades"
    n: int = Field(ge=0)
    integer_only: ClassVar[bool] = True

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        if m.buying is None or m.selling is None:
            msg = 'max_trades counts trades, which needs "solve": {"integer": true}'
            raise ValueError(msg)
        members = m.scope(self.sleeve).members.astype(float)
        return [members @ (m.buying + m.selling) <= self.n]
