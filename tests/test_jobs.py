"""One smoke test per entry point, over a small synthetic book and the example strategies."""

import json
from pathlib import Path

import httpx

from optim.jobs.model_portfolio import ModelPortfolioRequest, model_portfolio
from optim.jobs.optimize import OptimizeRequest, optimize
from optim.jobs.transition import TransitionRequest, transition
from optim.optimizer.results import Solved
from optim.optimizer.rounding import TOLERANCE
from tests.builders import AS_OF, context, strategy, synthetic_book


def test_optimize_snapshots_the_run_before_it_submits_the_orders(tmp_path: Path) -> None:
    posted: list[object] = []

    def oms(request: httpx.Request) -> httpx.Response:
        assert [p.name for p in tmp_path.iterdir() if not p.name.startswith(".")], "orders went out before the snapshot"
        posted.extend(json.loads(request.content))
        return httpx.Response(200)

    request = OptimizeRequest(strategy=strategy("core-muni"), accounts=("A0", "A1", "A2"), as_of=AS_OF, workers=2, submit=True)
    results = optimize(context(synthetic_book(accounts=3), tmp_path, oms=oms), request)
    assert all(isinstance(r, Solved) and r.ok for r in results), results
    [run] = tmp_path.iterdir()
    assert {p.name for p in run.iterdir()} == {f"{name}.parquet" for name in ("securities", "targets", "accounts", "lots", "trades", "positions", "summary")} | {"run.json"}
    snapshot = json.loads((run / "run.json").read_text())
    assert snapshot["request"]["accounts"] == ["A0", "A1", "A2"]
    assert posted
    assert posted == snapshot["orders"]
    assert all(order["par"] % 5000 == 0 for order in snapshot["orders"])


def test_model_portfolio_invests_the_notional_within_limits(tmp_path: Path) -> None:
    request = ModelPortfolioRequest(strategy=strategy("track-benchmark"), benchmark_id="BM", notional=100_000_000, as_of=AS_OF)
    [result] = model_portfolio(context(synthetic_book(accounts=1), tmp_path), request)
    assert isinstance(result, Solved)
    assert result.positions["weight"].max() <= 0.05 + TOLERANCE  # buys round down, so limits survive rounding
    assert result.summary["cash"] < 0.05 * result.summary["value"]


def test_transition_frontier_never_gets_worse_as_the_tax_budget_grows(tmp_path: Path) -> None:
    budgets = (0.0, 2_000.0, 20_000.0)
    request = TransitionRequest(strategy=strategy("track-benchmark"), account_id="A0", tax_budgets=budgets, as_of=AS_OF)
    results = transition(context(synthetic_book(accounts=1), tmp_path), request)
    solved = [r for r in results if isinstance(r, Solved)]
    assert len(solved) == len(budgets)
    objectives = [r.summary["objective"] for r in solved]
    assert objectives == sorted(objectives)
    assert all(r.summary["tax"] <= budget + 1 for r, budget in zip(solved, budgets, strict=True))
