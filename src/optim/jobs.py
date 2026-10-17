"""Entry points. Each one is load -> solve -> snapshot -> act, built from the same parts. They differ only in which
portfolios they solve, with which strategies, and what happens to the results."""

import uuid
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx

from optim.data import Loader, empty_frame
from optim.inputs import Account, Lot, Portfolio, load_market, load_portfolios
from optim.lp import MaxTax, Result
from optim.outputs import input_frames, orders_for, result_frames, save_snapshot, submit_orders
from optim.run import Strategy, Task, solve_all
from optim.util import timed


def optimize(loader: Loader, account_ids: Sequence[str], strategy: Strategy, *, as_of: date, workers: int, snapshots: Path, oms: httpx.Client | None) -> list[Result]:
    """Rebalance accounts. Orders go out only for clean results, and only after the run's snapshot is saved."""
    run_id = str(uuid.uuid7())
    market = load_market(loader, as_of)
    portfolios = load_portfolios(loader, account_ids)
    with timed("solve", run_id=run_id, tasks=len(portfolios)):
        results = solve_all(market, [Task(p, strategy) for p in portfolios], workers=workers)
    orders = orders_for(run_id, results)
    meta = {"run_id": run_id, "job": "optimize", "as_of": as_of, "strategy": strategy, "orders": orders}
    save_snapshot(snapshots, run_id, meta=meta, frames=input_frames(market, portfolios) | result_frames(results))
    if oms is not None and orders:
        submit_orders(oms, orders)
    return results


def model_portfolio(loader: Loader, strategy: Strategy, *, benchmark_id: str, notional: float, as_of: date, snapshots: Path) -> list[Result]:
    """Build a model portfolio from cash: the same engine, run for an all-cash account that pays no tax."""
    run_id = str(uuid.uuid7())
    market = load_market(loader, as_of)
    account = Account(account_id=f"model:{strategy.name}", benchmark_id=benchmark_id, cash=notional, state=None, federal_rate=0.0, state_rate=0.0, st_gain_rate=0.0, lt_gain_rate=0.0)
    portfolios = [Portfolio(account, empty_frame(Lot))]
    results = solve_all(market, [Task(portfolios[0], strategy)], workers=1)
    meta = {"run_id": run_id, "job": "model_portfolio", "as_of": as_of, "strategy": strategy}
    save_snapshot(snapshots, run_id, meta=meta, frames=input_frames(market, portfolios) | result_frames(results))
    return results


def transition(loader: Loader, account_id: str, strategy: Strategy, *, tax_budgets: Sequence[float], as_of: date, workers: int, snapshots: Path) -> list[Result]:
    """How close to its target an account can get for each tax budget: one solve per budget, read as a frontier."""
    run_id = str(uuid.uuid7())
    market = load_market(loader, as_of)
    portfolios = load_portfolios(loader, [account_id])
    tasks = [Task(portfolios[0], _with_tax_budget(strategy, budget)) for budget in tax_budgets]
    results = solve_all(market, tasks, workers=workers)
    meta = {"run_id": run_id, "job": "transition", "as_of": as_of, "strategy": strategy, "tax_budgets": tax_budgets}
    save_snapshot(snapshots, run_id, meta=meta, frames=input_frames(market, portfolios) | result_frames(results))
    return results


def _with_tax_budget(strategy: Strategy, dollars: float) -> Strategy:
    return Strategy(name=f"{strategy.name}|tax<={dollars:.0f}", rules=strategy.rules, objective=strategy.objective, constraints=(*strategy.constraints, MaxTax(dollars=dollars)))
