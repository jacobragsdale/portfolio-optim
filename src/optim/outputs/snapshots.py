"""A snapshot of every run: its request, everything the solves read and everything they returned, so any result can be
explained and replayed. One directory per run, holding `run.json` and a Parquet file per frame."""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd
import pydantic_core

from optim.inputs import Market, Portfolio
from optim.optimizer.results import Result, Solved, Unsolved
from optim.schema import Strict

log = logging.getLogger(__name__)


def save_run(root: Path, run_id: str, *, job: str, request: Strict, market: Market, portfolios: Sequence[Portfolio], results: Sequence[Result], **extra: object) -> Path:
    meta = {"run_id": run_id, "job": job, "request": request, **extra}
    return save_snapshot(root, run_id, meta=meta, frames=input_frames(market, portfolios) | result_frames(results))


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
    # ponytail: writes the whole market every run; store it once per content hash if snapshots grow too fast
    return {
        "securities": market.securities,
        "targets": pd.concat(market.targets, names=["benchmark_id"]).reset_index(),
        "accounts": pd.DataFrame([p.account.model_dump() for p in portfolios]),
        "lots": pd.concat([p.lots for p in portfolios], ignore_index=True),
    }


def result_frames(results: Sequence[Result]) -> dict[str, pd.DataFrame]:
    solved = [r for r in results if isinstance(r, Solved)]
    return {
        "trades": _concat([r.trades.assign(strategy=r.strategy) for r in solved]),
        "positions": _concat([r.positions.assign(account_id=r.account_id, strategy=r.strategy) for r in solved]),
        "summary": pd.DataFrame([_summary_row(r) for r in results]),
    }


def _summary_row(result: Result) -> dict[str, object]:
    match result:
        case Solved():
            status = "ok" if result.ok else f"rounding broke {sorted(result.violations)}"
            return {"account_id": result.account_id, "strategy": result.strategy, "status": status, **result.summary, "seconds": result.seconds}
        case Unsolved():
            return {"account_id": result.account_id, "strategy": result.strategy, "status": result.status}


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
