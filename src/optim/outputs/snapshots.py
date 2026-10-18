"""A snapshot of every run: its request, the datasets it loaded and everything its solves returned, so any result can be
explained and replayed. One directory per run, holding `run.json`, a Parquet file per result frame, and `inputs/`, a
Parquet file per dataset exactly as loaded. What the solves derived from those is not saved: replay rebuilds it with the
same code."""

import logging
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Self

import pandas as pd
from pydantic import TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict

from optim.data.frames import to_columns
from optim.datasets import all_datasets
from optim.optimizer.results import Result, result_frames
from optim.schema import Model

log = logging.getLogger(__name__)
_RUN_JSON = TypeAdapter(dict[str, object])


class _Built(BaseSettings):
    """What an image says about itself: the commit, baked in at build (`--build-arg GIT_SHA`), and the image, which the
    Job sets."""

    model_config = SettingsConfigDict(env_prefix="OPTIM_", extra="forbid", frozen=True)

    git_sha: str | None = None
    image: str | None = None


class Code(Model):
    """The code a run ran, as `run.json` records it."""

    git_sha: str | None
    """The commit: `OPTIM_GIT_SHA`, baked into the image, or else the checkout's `HEAD`. Null when neither is known."""
    image: str | None
    """The image, from `OPTIM_IMAGE`, which the Job sets. Null outside an image."""
    dirty: bool | None
    """Whether the checkout had changes not yet committed, so `git_sha` is not the whole story. Null in an image."""

    @classmethod
    def current(cls) -> Self:
        built = _Built()
        if built.git_sha is not None:
            return cls(git_sha=built.git_sha, image=built.image, dirty=None)
        head, dirty = _checkout()
        return cls(git_sha=head, image=built.image, dirty=dirty)


def save_run(root: Path, run_id: str, *, meta: Model, frames: Mapping[str, pd.DataFrame], results: Sequence[Result]) -> Path:
    """`meta` is the run's `run.json`; `frames` are the datasets the run loaded, by name, as `load_frames` returns them."""
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


def _write(root: Path, run_id: str, *, meta: Model, frames: Mapping[str, pd.DataFrame]) -> Path:
    """Write `root/run_id/`, a frame per `frames` key, which may name a subdirectory. The directory appears only once
    it is complete."""
    partial, path = root / f".{run_id}.partial", root / run_id
    partial.mkdir(parents=True)
    (partial / "run.json").write_text(meta.model_dump_json(indent=2))
    for name, frame in frames.items():
        file = partial / f"{name}.parquet"
        file.parent.mkdir(exist_ok=True)
        frame.to_parquet(file, index=False)
    partial.rename(path)
    log.info("saved snapshot %s", path, extra={"run_id": run_id})
    return path


def _checkout() -> tuple[str | None, bool | None]:
    """The checkout this package runs from: its `HEAD`, and whether it has changes not yet committed. Neither outside
    one, as for an installed package."""
    here = Path(__file__).parent
    try:
        head = _git(here, "rev-parse", "HEAD")
        status = _git(here, "status", "--porcelain")
    except OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired:  # no git, or not a checkout
        return None, None
    return head, bool(status)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=10).stdout.strip()  # noqa: S603, S607 -- fixed arguments; git from PATH, as a developer runs it
