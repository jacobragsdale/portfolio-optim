"""The cvxpy problem, built from definitions: the example's stand-in for the firm's cvxpy wrapper, which does this in a
firm model. `build` takes the portfolio's universe, its constraints and its objective, as dicts, and returns the
unsolved problem, `buy` and `sell`, a variable each per row of the universe, and each objective term's expression,
which `solve` reports as a metric. To move to the wrapper, delete this file and build with the wrapper in `solve.py`.

The constraints it compiles:

    {"kind": "weight", "per": "issuer", "max": 0.1}                     each issuer at most 10% of value after trading
    {"kind": "weight", "where": {"sector": "Financials"}, "max": 0.25}  the Financials together at most 25%
    {"kind": "turnover", "max": 0.25}                                   buys plus sales at most 25% of value

`per` and `where` take any column of the universe, such as `security_id` or `sector`; a weight cap with neither caps
every row together. A weight cap leaves the portfolio what it may not sell: it may keep that, however much it weighs.

The objective's terms, which it maximizes, summed, as a fraction of the portfolio's value:

    {"term": "income"}              expected income over a year after trading
    {"term": "cost", "bps": 5.0}    less the trading cost, in basis points of the value traded
"""

from dataclasses import dataclass
from typing import Annotated, Literal

import cvxpy as cp
import numpy as np
import pandas as pd
import scipy.sparse as sp
from optim.schema import Model
from pydantic import Field, TypeAdapter

from optim.example import model


class Weight(Model):
    """The weight of each group of rows, `per` a column, or of the rows `where` picks, at most `max` after trading."""

    kind: Literal["weight"]
    max: float = Field(ge=0)
    per: str | None = None
    where: dict[str, str] = Field(default_factory=dict)


class Turnover(Model):
    """The value bought plus the value sold at most `max` of the portfolio's value."""

    kind: Literal["turnover"]
    max: float = Field(ge=0)


class Income(Model):
    """Expected income over a year after trading."""

    term: Literal["income"]


class Cost(Model):
    """The estimated trading cost, which the objective subtracts."""

    term: Literal["cost"]
    bps: float = Field(ge=0)


_CONSTRAINTS = TypeAdapter(list[Annotated[Weight | Turnover, Field(discriminator="kind")]])
_TERMS = TypeAdapter(list[Annotated[Income | Cost, Field(discriminator="term")]])


@dataclass(frozen=True)
class Built:
    """An unsolved problem, and the expressions that read its answer."""

    problem: cp.Problem
    buy: cp.Variable
    """Quantity bought of each row of the universe, in the holdings' units."""
    sell: cp.Variable
    """Quantity sold of each row."""
    terms: dict[str, cp.Expression]
    """Each objective term's amount, in the portfolio's currency, by the term's name."""


def build(universe: pd.DataFrame, constraints: list[model.Definition], objective: list[model.Definition], *, value: float) -> Built:
    """The problem the definitions describe, for a universe of at least one row. A definition it does not know fails,
    naming what it knows."""
    unit, held = (universe[column].to_numpy(dtype=float) for column in ("unit_value", "held"))
    buy, sell = cp.Variable(len(universe), nonneg=True), cp.Variable(len(universe), nonneg=True)
    weights = cp.multiply(unit / value, held + buy - sell)
    kept = np.where(universe["sellable"].to_numpy(dtype=bool), 0.0, unit * held / value)  # what it may not sell, it may keep
    limits = []
    for constraint in _CONSTRAINTS.validate_python(constraints):
        if isinstance(constraint, Turnover):
            limits.append((unit / value) @ (buy + sell) <= constraint.max)
        else:
            groups = _groups(constraint, universe)
            limits.append(sp.csr_array(groups) @ weights <= np.maximum(constraint.max, groups @ kept))
    terms, signed = {}, []
    for term in _TERMS.validate_python(objective):
        if isinstance(term, Income):
            terms["income"] = (universe["expected_return"].to_numpy(dtype=float) * unit) @ (held + buy - sell)
            signed.append(terms["income"])
        else:
            terms["cost"] = (unit * term.bps / 1e4) @ (buy + sell)
            signed.append(-terms["cost"])
    return Built(cp.Problem(cp.Maximize(sum(signed) / value), limits), buy, sell, terms)


def _groups(weight: Weight, universe: pd.DataFrame) -> np.ndarray:
    """A row per group the cap applies to and a column per row of the universe, 1 where the row is in the group."""
    if weight.per is not None:
        return pd.get_dummies(universe[weight.per]).to_numpy(dtype=float).T
    picked = np.ones(len(universe), dtype=bool)
    for column, wanted in weight.where.items():
        picked &= (universe[column] == wanted).to_numpy(dtype=bool)
    return picked.astype(float)[np.newaxis]
