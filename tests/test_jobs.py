"""One smoke test per entry point, over a small synthetic book and the example strategies."""

import json
from pathlib import Path

from optim.jobs import model_portfolio, optimize, transition
from optim.lp import TOLERANCE, Solved
from tests.builders import AS_OF, loader, strategy, synthetic_book


def test_optimize_solves_a_batch_in_worker_processes_and_snapshots_it(tmp_path: Path) -> None:
    load, _ = loader(synthetic_book(accounts=3))
    results = optimize(load, ["A0", "A1", "A2"], strategy("core-muni"), as_of=AS_OF, workers=2, snapshots=tmp_path, oms=None)
    assert all(isinstance(r, Solved) and r.ok for r in results), results
    [run] = tmp_path.iterdir()
    assert {p.name for p in run.iterdir()} == {f"{name}.parquet" for name in ("securities", "targets", "accounts", "lots", "trades", "positions", "summary")} | {"run.json"}
    orders = json.loads((run / "run.json").read_text())["orders"]
    assert orders
    assert all(order["par"] % 5000 == 0 for order in orders)


def test_model_portfolio_invests_the_notional_within_limits(tmp_path: Path) -> None:
    load, _ = loader(synthetic_book(accounts=1))
    [result] = model_portfolio(load, strategy("track-benchmark"), benchmark_id="BM", notional=100_000_000, as_of=AS_OF, snapshots=tmp_path)
    assert isinstance(result, Solved)
    assert result.positions["weight"].max() <= 0.05 + TOLERANCE  # buys round down, so limits survive rounding
    assert result.summary["cash"] < 0.05 * result.summary["value"]


def test_transition_frontier_never_gets_worse_as_the_tax_budget_grows(tmp_path: Path) -> None:
    load, _ = loader(synthetic_book(accounts=1))
    budgets = [0.0, 2_000.0, 20_000.0]
    results = transition(load, "A0", strategy("track-benchmark"), tax_budgets=budgets, as_of=AS_OF, workers=1, snapshots=tmp_path)
    solved = [r for r in results if isinstance(r, Solved)]
    assert len(solved) == len(budgets)
    objectives = [r.summary["objective"] for r in solved]
    assert objectives == sorted(objectives)
    assert all(r.summary["tax"] <= budget + 1 for r, budget in zip(solved, budgets, strict=True))
