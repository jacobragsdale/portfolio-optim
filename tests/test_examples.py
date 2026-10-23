"""The examples `optim new` copies, run end to end on their fixtures, as a model repo's contract tests run them."""

import json
from datetime import date
from pathlib import Path

import pytest
from builders import EXAMPLES, Rows, loader

from optim import models
from optim.pytest_plugin import check_fixture, check_imports, check_params
from optim.run import replay, run
from optim.snapshots import read_results
from optim.storage import Root


@pytest.mark.parametrize("name", ["cvxpy", "plain"])
def test_each_example_keeps_the_contract_a_model_repo_tests(name: str) -> None:
    model, example = models.load(f"optim.examples.{name}"), EXAMPLES / name

    check_params(model, example / "params" / "default.json")
    check_fixture(model, example / "fixture", example / "params" / "default.json")
    check_imports(model)


def run_example(name: str, tmp_path: Path) -> tuple[Root, str]:
    root, example = Root.at(tmp_path), EXAMPLES / name
    as_of = date.fromisoformat(json.loads((example / "fixture" / "fixture.json").read_text())["as_of_date"])
    ran = run(loader(Rows({})), root, models.load(f"optim.examples.{name}"), example / "params" / "default.json", as_of_date=as_of, data_dir=example / "fixture")
    return root, ran.run_id


def test_the_cvxpy_example_rebalances_within_its_limits_and_controls(tmp_path: Path) -> None:
    """P1 holds 9% in B, over its 5% limit, and a wash-sale control on C; D is rated below the floor. P2 holds cash."""
    root, run_id = run_example("cvxpy", tmp_path)

    results = read_results(root / run_id)
    trades = results["trades"].set_index(["portfolio_id", "security_id"])["quantity"]

    assert results["decisions"]["status"].tolist() == ["accepted", "accepted"]
    assert trades.to_dict() == {("P1", "A"): 54_000.0, ("P1", "B"): 46_000.0, ("P2", "A"): 24_000.0, ("P2", "B"): 24_000.0, ("P2", "C"): 24_000.0}
    assert set(results["metrics"]["metric"]) == {"income", "cost"}


def test_the_cvxpy_example_replays_to_the_same_trades(tmp_path: Path) -> None:
    root, run_id = run_example("cvxpy", tmp_path)

    replayed = replay(root / run_id, root)

    assert read_results(root / replayed.run_id)["trades"].equals(read_results(root / run_id)["trades"])


def test_the_plain_example_trades_every_portfolio_toward_the_targets(tmp_path: Path) -> None:
    """P1 holds A over its 30% target and D, which no target names; P2 holds cash."""
    root, run_id = run_example("plain", tmp_path)

    results = read_results(root / run_id)
    trades = results["trades"].set_index(["portfolio_id", "security_id", "side"])["quantity"]

    assert results["decisions"]["status"].tolist() == ["accepted", "accepted"]
    assert trades[("P1", "D", "sell")] == 200_000.0  # sold whole
    assert trades[("P1", "A", "sell")] > 0
    assert {security for portfolio, security, side in trades.index if portfolio == "P2"} == {"A", "B", "C"}
