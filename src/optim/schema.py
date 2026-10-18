"""Pydantic building blocks for every boundary: the base models, and the field types for bonds and rates that rows,
strategies and requests share."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


class Model(BaseModel):
    """Base for every model that crosses a boundary: settings, job requests, database rows. Lax, so exact
    conversions pass (a driver's `Decimal`, Oracle's midnight `datetime` for a date, an ISO string, `1000.0` for an int)
    and lossy ones fail (`1000.5` for an int, `5` for a str). Document a field with a docstring under it, not a comment:
    it becomes the field's description in the model's JSON schema, which `optim schema` prints for strategies."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False, use_attribute_docstrings=True)


def _kind_required(schema: dict[str, JsonValue]) -> None:
    """`kind` has a default so Python can write `MaxTurnover(max_weight=0.2)`, but JSON must name it to pick the union member."""
    properties, required = schema.get("properties"), schema.setdefault("required", [])
    if isinstance(properties, dict) and "kind" in properties and isinstance(required, list) and "kind" not in required:
        required.insert(0, "kind")


class StrictModel(Model):
    """Base for the strategy JSON that traders and agents write. JSON has a type for every field, so `"0.05"` or `true`
    where a weight belongs is a misread contract, not data to coerce. Agents read its field docstrings in `optim schema`."""

    model_config = ConfigDict(strict=True, json_schema_extra=_kind_required)


class Row(Model):
    """Schema of one dataset row. Field names are column names; annotations pick the pandas dtype (`optim.data.frames`).
    Field docstrings describe the columns."""


Cusip = Annotated[str, StringConstraints(pattern=r"^[0-9A-Z*@#]{8}[0-9]$")]
Rate = Annotated[float, Field(ge=0, lt=1)]
Price = Annotated[float, Field(gt=0)]  # per 100 par
Par = Annotated[int, Field(gt=0)]  # face amount in whole dollars: trades are exact, the solver works in float64
Weight = Annotated[float, Field(ge=0, le=1)]
Rating = Literal[
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D",
]  # fmt: skip  # best first: an ordered category, so `rating <= "A-"` means "A- or better"
