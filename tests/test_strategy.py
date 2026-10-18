"""The strategy contract: what its schema tells agents, and what it refuses before any solve."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from optim.strategy import Strategy

STRATEGIES = sorted(Path(__file__).parent.parent.joinpath("strategies").glob("*.json"))


def test_schema_requires_kind_and_documents_fields() -> None:
    defs = Strategy.model_json_schema()["$defs"]
    assert "kind" in defs["MaxTurnover"]["required"]
    assert "kind" in defs["Cost"]["required"]  # no other required field
    assert "best solution" in defs["SolveSettings"]["properties"]["time_limit_s"]["description"]
    assert "sleeve" in defs["PositionLimit"]["properties"]


@pytest.mark.parametrize("path", STRATEGIES, ids=lambda path: path.stem)
def test_every_shipped_strategy_is_valid(path: Path) -> None:
    Strategy.model_validate_json(path.read_bytes())


QUADRATIC = {"kind": "turnover_quadratic", "penalty": 0.01}


@pytest.mark.parametrize(
    ("strategy", "error"),
    [
        ({"objective": [QUADRATIC], "solve": {"integer": True, "solver": "HIGHS"}}, 'HIGHS cannot solve a quadratic objective .turnover_quadratic. with integer trades: use "solver": "COPT"'),
        ({"constraints": [{"kind": "max_trades", "n": 5}], "solve": {"integer": False}}, "max_trades counts trades"),
        ({"objective": [{"kind": "ladder_balance", "first_year": 5, "last_year": 2, "penalty": 1.0}]}, "last_year 2 must be after first_year 5"),
        ({"constraints": [{"kind": "exposure", "column": "duration"}]}, "needs min, max or both"),
        ({"constraints": [{"kind": "exposure", "column": "yield", "max": 0.1}]}, "column"),
    ],
    ids=["miqp-on-highs", "count-in-lp", "ladder-backwards", "unbounded-exposure", "unknown-analytic"],
)
def test_a_strategy_its_solve_cannot_solve_as_written_is_refused_when_read(strategy: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Strategy.model_validate({"name": "s"} | strategy)


@pytest.mark.parametrize("solve", [{"integer": True, "solver": "COPT"}, {"integer": False, "solver": "HIGHS"}], ids=["copt-miqp", "highs-qp"])
def test_a_quadratic_objective_is_read_where_its_solver_can_solve_it(solve: dict[str, object]) -> None:
    Strategy.model_validate({"name": "s", "objective": [QUADRATIC], "solve": solve})
