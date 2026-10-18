from optim.strategy import Strategy


def test_schema_requires_kind_and_documents_fields() -> None:
    defs = Strategy.model_json_schema()["$defs"]
    assert "kind" in defs["MaxTurnover"]["required"]
    assert "kind" in defs["Cost"]["required"]  # no other required field
    assert "best solution" in defs["SolveSettings"]["properties"]["time_limit_s"]["description"]
