"""A snapshot of every run: its request, everything the solves read and everything they returned, so any result can be
explained and replayed. One directory per run, holding `run.json` and a Parquet file per frame."""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import pydantic_core

from optim.data.frames import empty_frame, to_models
from optim.datasets.portfolios import LossSale, Lot, PortfolioDetails
from optim.inputs import Market, Portfolio, by_portfolio
from optim.optimizer.results import Result, Solved, Unsolved
from optim.schema import Model

log = logging.getLogger(__name__)


def save_run(root: Path, run_id: str, *, job: str, request: Model, market: Market, portfolios: Sequence[Portfolio], results: Sequence[Result], **extra: object) -> Path:
    meta = {"run_id": run_id, "job": job, "as_of_date": market.as_of_date, "request": request, **extra}
    return save_snapshot(root, run_id, meta=meta, frames=input_frames(market, portfolios) | result_frames(results))


def load_run(path: Path) -> tuple[Market, list[Portfolio]]:
    """A saved run's market and portfolios, as its solves read them before the rules, for `optim replay`."""
    as_of_date = date.fromisoformat(json.loads((path / "run.json").read_bytes())["as_of_date"])
    targets = pd.read_parquet(path / "targets.parquet")
    market = Market(as_of_date, pd.read_parquet(path / "securities.parquet"), {str(b): group.set_index("cusip")["weight"] for b, group in targets.groupby("benchmark_id")})
    lots, loss_sales = by_portfolio(pd.read_parquet(path / "lots.parquet")), by_portfolio(pd.read_parquet(path / "loss_sales.parquet"))
    details = to_models(PortfolioDetails, pd.read_parquet(path / "portfolios.parquet"))
    return market, [Portfolio(d, lots.get(d.portfolio_id, empty_frame(Lot)), loss_sales.get(d.portfolio_id, empty_frame(LossSale))) for d in details]


def save_snapshot(root: Path, run_id: str, *, meta: Mapping[str, object], frames: Mapping[str, pd.DataFrame]) -> Path:
    """Write `root/run_id/`. The directory appears only once it is complete."""
    partial, path = root / f".{run_id}.partial", root / run_id
    partial.mkdir(parents=True)
    (partial / "run.json").write_bytes(pydantic_core.to_json(meta, indent=2))
    for name, frame in frames.items():
        frame.to_parquet(partial / f"{name}.parquet", index=False)
    partial.rename(path)
    log.info("saved snapshot %s", path, extra={"run_id": run_id})
    return path


def input_frames(market: Market, portfolios: Sequence[Portfolio]) -> dict[str, pd.DataFrame]:
    # writes the whole market every run; store it once per content hash if snapshots grow too fast
    return {
        "securities": market.securities,
        "targets": pd.concat(market.targets, names=["benchmark_id", "cusip"]).rename("weight").reset_index(),  # `load_run` reads these names
        "portfolios": pd.DataFrame([p.details.model_dump() for p in portfolios]),
        "lots": pd.concat([p.lots for p in portfolios], ignore_index=True),
        "loss_sales": pd.concat([p.loss_sales for p in portfolios], ignore_index=True),
    }


def result_frames(results: Sequence[Result]) -> dict[str, pd.DataFrame]:
    solved = [r for r in results if isinstance(r, Solved)]
    return {
        "trades": _concat([r.trades.assign(strategy=r.strategy) for r in solved]),
        "positions": _concat([r.positions.assign(portfolio_id=r.portfolio_id, strategy=r.strategy) for r in solved]),
        "summary": pd.DataFrame([_summary_row(r) for r in results]),
    }


def _summary_row(result: Result) -> dict[str, object]:
    match result:
        case Solved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status, **result.summary, "seconds": result.seconds}
        case Unsolved():
            return {"portfolio_id": result.portfolio_id, "strategy": result.strategy, "status": result.status}


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
