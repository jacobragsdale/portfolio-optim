"""Benchmark constituent weights, from Oracle. They change at the close, so they may be up to 12 hours old."""

from datetime import date, timedelta
from typing import Annotated

import pandas as pd
from pydantic import Field

from optim.data.loader import CachePolicy, Dataset, Loader
from optim.schema import Id, Row
from optim.sources import Param


class BenchmarkWeight(Row):
    benchmark_id: Id
    security_id: Id
    weight: Annotated[float, Field(gt=0, le=1)]
    """Share of the benchmark's market value. A benchmark's weights sum to 1."""


def _weights_sum_to_one(frame: pd.DataFrame) -> None:
    totals = frame.groupby("benchmark_id")["weight"].sum()
    if not (bad := totals[(totals - 1).abs() > 1e-6]).empty:
        msg = f"benchmark weights must sum to 1: {bad.head().to_dict()}"
        raise ValueError(msg)


def _by_date(loader: Loader, as_of_date: date) -> list[dict[str, Param]]:  # noqa: ARG001
    return [{"as_of_date": as_of_date}]


BENCHMARKS = Dataset(
    "benchmarks",
    BenchmarkWeight,
    source="oracle",
    query="datasets/sql/benchmarks.sql",
    key=("benchmark_id", "security_id"),
    cache=CachePolicy(ttl=timedelta(hours=12), warm=_by_date),
    check=_weights_sum_to_one,
)
