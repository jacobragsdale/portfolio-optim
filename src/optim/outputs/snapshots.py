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

from optim.data.frames import to_columns
from optim.datasets import all_datasets
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
    # writes the whole market every run; store it once per content hash if snapshots grow too fast
    inputs = {f"inputs/{name}": frame for name, frame in frames.items()}
    return _write(root, run_id, meta=meta, frames=inputs | result_frames(results))


def load_run(path: Path) -> tuple[dict[str, object], dict[str, pd.DataFrame]]:
    """A saved run's `run.json` and the datasets it loaded, by name, for `optim replay`. Each dataset is validated as a
    load validates it, so a snapshot saved before a schema change, or written by hand, fails here, naming the column."""
    if not (inputs := path / "inputs").is_dir():
        msg = f"{path} has no inputs/ directory: it is not a snapshot, or was saved before snapshots kept each dataset as loaded"
        raise FileNotFoundError(msg)
    meta = _RUN_JSON.validate_json((path / "run.json").read_bytes())
    datasets = {dataset.name: dataset for dataset in all_datasets()}
    frames = {}
    for file in sorted(inputs.glob("*.parquet")):
        if file.stem not in datasets:
            msg = f"{file}: no dataset is named {file.stem!r}"
            raise ValueError(msg)
        try:
            frames[file.stem] = datasets[file.stem].to_frame(to_columns(pd.read_parquet(file)))
        except ValueError as error:
            error.add_note(f"while reading {file}")
            raise
    return meta, frames


def _write(root: Path, run_id: str, *, meta: Mapping[str, object], frames: Mapping[str, pd.DataFrame]) -> Path:
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
