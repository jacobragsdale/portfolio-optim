"""This model's rules: plain functions of frames, tested on their own. `weights` says where a portfolio is, `toward`
the trades that bring it to its targets, and `drift` how far it was."""

import pandas as pd
from optim.datasets import unit_value


def prices(securities: pd.DataFrame) -> pd.Series:
    """What one unit of each security is worth, by security id."""
    return pd.Series(unit_value(securities).to_numpy(), index=securities["security_id"])


def worth(holdings: pd.DataFrame, securities: pd.DataFrame) -> float:
    """The portfolio's value, cash included."""
    return float((holdings.set_index("security_id")["quantity"] * prices(securities)).sum())


def weights(holdings: pd.DataFrame, securities: pd.DataFrame) -> pd.Series:
    """Each holding's weight of the portfolio's value, by security id, cash aside: cash is what the weights leave."""
    cash = securities.loc[securities["asset_class"] == "cash", "security_id"]
    held = holdings[~holdings["security_id"].isin(cash)].set_index("security_id")["quantity"]
    return held * prices(securities).reindex(held.index) / worth(holdings, securities)


def toward(weights: pd.Series, targets: dict[str, float], *, value: float, prices: pd.Series, tolerance: float) -> tuple[pd.Series, pd.Series]:
    """Quantities to buy and to sell, by security id: each target security to its weight, unless it is within
    `tolerance` of it, and every holding the targets leave out sold whole. The core rounds them to each security's
    increment."""
    wanted = pd.Series(targets, dtype=float).reindex(weights.index.union(pd.Index(list(targets))), fill_value=0.0)
    gap = wanted - weights.reindex(wanted.index, fill_value=0.0)
    gap = gap[(gap.abs() > tolerance) | (wanted == 0)]
    quantities = gap * value / prices.reindex(gap.index)
    return quantities[quantities > 0], -quantities[quantities < 0]


def drift(weights: pd.Series, targets: dict[str, float]) -> float:
    """The sum of the absolute differences between the weights and the targets."""
    wanted = pd.Series(targets, dtype=float)
    index = wanted.index.union(weights.index)
    return float((wanted.reindex(index, fill_value=0.0) - weights.reindex(index, fill_value=0.0)).abs().sum())
