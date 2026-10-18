"""Concentration limits: per security, and per issuer, sector or rating, in a scope."""

from typing import Literal

import cvxpy as cp
import numpy as np
from pydantic import Field

from optim.optimizer.model import OptimizationModel, Scoped, indicator
from optim.schema import Weight


class PositionLimit(Scoped):
    """No security above `max_weight` of the scope, across the scope's sleeves."""

    kind: Literal["position_limit"] = "position_limit"
    max_weight: Weight

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        scope = m.scope(self.sleeve)
        securities, members = indicator(m.lines["security_id"].where(scope.members))
        return [members @ m.weights(scope) <= self.max_weight] if len(securities) else []


class GroupLimit(Scoped):
    """Cap the weight of each issuer, sector or rating in the scope. `overrides` sets the cap for named groups."""

    kind: Literal["group_limit"] = "group_limit"
    by: Literal["issuer", "sector", "rating"]
    max_weight: Weight
    overrides: dict[str, Weight] = Field(default_factory=dict)

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        scope = m.scope(self.sleeve)
        groups, members = indicator(m.lines[self.by].where(scope.members))
        if not len(groups):
            return []
        caps = np.array([self.overrides.get(str(group), self.max_weight) for group in groups])
        return [members @ m.weights(scope) <= caps]
