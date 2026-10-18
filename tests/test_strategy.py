import pytest
from pydantic import ValidationError

from optim.strategy import Strategy


def test_schema_requires_kind_and_documents_fields() -> None:
    defs = Strategy.model_json_schema()["$defs"]
    assert "kind" in defs["MaxTax"]["required"]
    assert "kind" in defs["Cost"]["required"]  # no other required field
    assert "best solution" in defs["SolveSettings"]["properties"]["time_limit_s"]["description"]


@pytest.mark.parametrize("item", ['{"rules": [{"kind": "maturity_range", "min_years": 10, "max_years": 2}]}', '{"constraints": [{"kind": "cash_range", "min_weight": 0.5, "max_weight": 0.1}]}'])
def test_reversed_range_is_refused(item: str) -> None:
    with pytest.raises(ValidationError, match="is above"):
        Strategy.model_validate_json('{"name": "x", ' + item[1:])
