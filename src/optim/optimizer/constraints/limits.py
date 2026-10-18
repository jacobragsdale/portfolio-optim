"""Concentration limits: per bond, and per issuer, sector or rating."""

from typing import Literal

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp
from pydantic import Field

from optim.optimizer.model import OptimizationModel
from optim.schema import StrictModel, Weight


class PositionLimit(StrictModel):
    """No bond above `max_weight` of the portfolio."""

    kind: Literal["position_limit"] = "position_limit"
    max_weight: Weight

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        return [m.weights <= self.max_weight]


class GroupLimit(StrictModel):
    """Cap the weight of each issuer, sector or rating. `overrides` sets the cap for named groups."""

    kind: Literal["group_limit"] = "group_limit"
    by: Literal["issuer", "sector", "rating"]
    max_weight: Weight
    overrides: dict[str, Weight] = Field(default_factory=dict)

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        groups, members = indicator(m.securities[self.by])
        if not len(groups):
            return []
        caps = np.array([self.overrides.get(str(group), self.max_weight) for group in groups])
        return [members @ m.weights <= caps]


def indicator(labels: pd.Series) -> tuple[pd.Index, sp.csr_array]:
    """The distinct labels, and a sparse groups x rows 0/1 membership matrix. Missing labels belong to no group."""
    codes, groups = pd.factorize(labels)
    rows = np.flatnonzero(codes >= 0)
    return pd.Index(groups), sp.csr_array((np.ones(len(rows)), (codes[rows], rows)), shape=(len(groups), len(labels)))
