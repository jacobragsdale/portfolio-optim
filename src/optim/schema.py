"""Pydantic building blocks for every boundary: the strict base model, and the field types for bonds, rates and dates
that rows, strategies, requests and orders share."""

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

STRICT = ConfigDict(strict=True, extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class Strict(BaseModel):
    """Base for every model that crosses a boundary: settings, job requests, strategy JSON, database rows, orders."""

    model_config = STRICT


class Row(Strict):
    """Schema of one dataset row. Field names are column names; annotations pick the pandas dtype (`optim.data.frames`)."""


def _as_date(value: object) -> object:
    """Oracle DATE (and pandas) hand back dates as midnight datetimes; JSON sends ISO strings."""
    if isinstance(value, datetime) and value.tzinfo is None and value.time() == time():
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


Date = Annotated[date, BeforeValidator(_as_date)]
Cusip = Annotated[str, StringConstraints(pattern=r"^[0-9A-Z*@#]{8}[0-9]$")]
Rate = Annotated[float, Field(ge=0, lt=1)]
Price = Annotated[float, Field(gt=0)]  # per 100 par
Par = Annotated[int, Field(gt=0)]  # face amount in whole dollars: orders are exact, the solver works in float64
Weight = Annotated[float, Field(ge=0, le=1)]
Rating = Literal[
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D",
]  # fmt: skip  # best first: an ordered category, so `rating <= "A-"` means "A- or better"
