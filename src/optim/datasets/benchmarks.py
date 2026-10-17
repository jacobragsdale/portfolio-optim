"""Benchmark constituent weights, from Oracle. They change at the close, so they may be up to 12 hours old."""

from datetime import timedelta
from typing import Annotated

import pandas as pd
from pydantic import Field

from optim.data.loader import CachePolicy, Dataset, by_date
from optim.schema import Cusip, Row


class BenchmarkWeight(Row):
    benchmark_id: str
    cusip: Cusip
    weight: Annotated[float, Field(gt=0, le=1)]


def _weights_sum_to_one(frame: pd.DataFrame) -> None:
    totals = frame.groupby("benchmark_id")["weight"].sum()
    if not (bad := totals[(totals - 1).abs() > 1e-6]).empty:
        msg = f"benchmark weights must sum to 1: {bad.head().to_dict()}"
        raise ValueError(msg)


BENCHMARKS = Dataset(
    "benchmarks",
    BenchmarkWeight,
    source="oracle",
    query="datasets/sql/benchmarks.sql",
    key=("benchmark_id", "cusip"),
    cache=CachePolicy(ttl=timedelta(hours=12), warm=by_date),
    check=_weights_sum_to_one,
)
