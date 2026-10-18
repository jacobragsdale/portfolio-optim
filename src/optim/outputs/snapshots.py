"""A snapshot of every run: its request, the datasets it loaded and everything its solves returned, so any result can be
explained and replayed. One directory per run, holding `run.json`, a Parquet file per result frame, and `inputs/`, a
Parquet file per dataset exactly as loaded. What the solves derived from those is not saved: replay rebuilds it with the
same code."""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import pydantic_core
from pydantic import TypeAdapter

from optim.optimizer.results import Result, Solved, Unsolved
from optim.schema import Model

log = logging.getLogger(__name__)
_RUN_JSON = TypeAdapter(dict[str, object])


def save_run(root: Path, run_id: str, *, job: str, request: Model, as_of_date: date, frames: Mapping[str, pd.DataFrame], results: Sequence[Result], **extra: object) -> Path:
    """`frames` are the datasets the run loaded, by name, as `load_frames` returns them."""
    meta = {"run_id": run_id, "job": job, "as_of_date": as_of_date, "request": request, **extra}
    return save_snapshot(root, run_id, meta=meta, frames=input_frames(frames) | result_frames(results))


def load_run(path: Path) -> tuple[dict[str, object], dict[str, pd.DataFrame]]:
    """A saved run's `run.json` and the datasets it loaded, by name, for `optim replay`."""
    if not (inputs := path / "inputs").is_dir():
        msg = f"{path} has no inputs/ directory: it is not a snapshot, or was saved before snapshots kept each dataset as loaded"
        raise FileNotFoundError(msg)
    meta = _RUN_JSON.validate_json((path / "run.json").read_bytes())
    return meta, {file.stem: pd.read_parquet(file) for file in sorted(inputs.glob("*.parquet"))}


def save_snapshot(root: Path, run_id: str, *, meta: Mapping[str, object], frames: Mapping[str, pd.DataFrame]) -> Path:
    """Write `root/run_id/`, a frame per `frames` key, which may name a subdirectory. The directory appears only once
    it is complete."""
    partial, path = root / f".{run_id}.partial", root / run_id
    partial.mkdir(parents=True)
    (partial / "run.json").write_bytes(pydantic_core.to_json(meta, indent=2))
    for name, frame in frames.items():
        file = partial / f"{name}.parquet"
        file.parent.mkdir(exist_ok=True)
        frame.to_parquet(file, index=False)
    partial.rename(path)
    log.info("saved snapshot %s", path, extra={"run_id": run_id})
    return path


def input_frames(frames: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """The datasets a run loaded, keyed as `save_snapshot` writes them and `load_run` reads them back."""
    # writes the whole market every run; store it once per content hash if snapshots grow too fast
    return {f"inputs/{name}": frame for name, frame in frames.items()}


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
