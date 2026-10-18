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
from pydantic_settings import BaseSettings, SettingsConfigDict

from optim.optimizer.results import Result, result_frames
from optim.schema import Model

log = logging.getLogger(__name__)
_RUN_JSON = TypeAdapter(dict[str, object])


class Code(BaseSettings):
    """The code a run ran, as `run.json` records it: the commit, baked into the image at build (`--build-arg GIT_SHA`),
    and the image, which the Job sets. A run outside an image records neither unless they are set."""

    model_config = SettingsConfigDict(env_prefix="OPTIM_", extra="forbid", frozen=True)

    git_sha: str | None = None
    image: str | None = None


def save_run(root: Path, run_id: str, *, job: str, request: Model, as_of_date: date, frames: Mapping[str, pd.DataFrame], results: Sequence[Result], **extra: object) -> Path:
    """`frames` are the datasets the run loaded, by name, as `load_frames` returns them."""
    meta = {"run_id": run_id, "job": job, "as_of_date": as_of_date, "code": Code(), "request": request, **extra}
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
