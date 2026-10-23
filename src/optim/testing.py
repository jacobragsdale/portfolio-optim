"""Builders for a model's behaviour tests: frames from a few partial rows, each filled from a valid default row, so a
test states only the fields it is about, and a model's `Data` from those frames. Every frame is validated by its
dataset's row model, as a load validates it.

    holdings = frame(HOLDINGS, {"security_id": "USD", "quantity": 1e6}, {"security_id": "A", "quantity": 50_000})
    data = optim.testing.data(Data, holdings=holdings, securities=frame(SECURITIES, CASH, {"security_id": "A"}))
"""

from collections.abc import Mapping
from datetime import date

import pandas as pd

from optim.contract import Datasets, declared
from optim.data.dataset import Dataset
from optim.data.frames import empty_frame

AS_OF = date(2026, 10, 16)

BOND: dict[str, object] = {
    "security_id": "A",
    "cusip": None,
    "isin": None,
    "asset_class": "bond",
    "currency": "USD",
    "issuer": "Issuer A",
    "sector": "Corporate",
    "rating": "AA",
    "maturity": date(2030, 10, 15),
    "price": 100.0,
    "accrued": 0.0,
    "price_per": 100,
    "increment": 1000.0,
    "min_piece": 1000.0,
    "min_remaining": 1000.0,
}
"""A bond priced at par, traded in increments of 1,000."""
CASH: dict[str, object] = BOND | {
    "security_id": "USD",
    "asset_class": "cash",
    "issuer": None,
    "sector": None,
    "rating": None,
    "maturity": None,
    "price": 1.0,
    "price_per": 1,
    "increment": 0.01,
    "min_piece": None,
    "min_remaining": None,
}
"""The cash security: a holding of it is the portfolio's cash, valued at 1 a unit."""
HOLDING: dict[str, object] = {"portfolio_id": "P1", "security_id": "USD", "quantity": 1_000_000.0}
"""P1 holds $1mm of cash."""

DEFAULTS: dict[str, Mapping[str, object]] = {"securities": BOND, "holdings": HOLDING}
"""The default row of each shared dataset, by name."""


def frame(dataset: Dataset, *rows: Mapping[str, object], default: Mapping[str, object] | None = None) -> pd.DataFrame:
    """`rows` as `dataset`'s validated frame. Each row's missing fields come from `default`, which is the shared
    datasets' default row for holdings and securities; a field neither gives is null."""
    base = DEFAULTS.get(dataset.name, {}) if default is None else default
    full = [dict(base) | dict(row) for row in rows]
    return dataset.to_frame({name: [row.get(name) for row in full] for name in dataset.row.model_fields}) if full else empty_frame(dataset.row)


def data[D: Datasets](cls: type[D], *, as_of_date: date = AS_OF, portfolio_id: str | None = None, **frames: pd.DataFrame) -> D:
    """A model's `Data` from frames by attribute name, such as `holdings=...`; a dataset not given has no rows."""
    datasets = declared(cls)
    if unknown := sorted(set(frames) - set(datasets)):
        msg = f"{cls.__qualname__} reads no datasets named {unknown}; it reads {sorted(datasets)}"
        raise TypeError(msg)
    return cls({dataset.name: frames[name] if name in frames else empty_frame(dataset.row) for name, dataset in datasets.items()}, as_of_date=as_of_date, portfolio_id=portfolio_id)
