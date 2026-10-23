"""Running a model by running a file, as quants do from an IDE. Each model's `__main__.py` calls `run` or `replay` with
its own `__file__`, and they run as `optim run` and `optim replay` do, through the same code. They find the model, its
default params and its fixture from the file's place in its repo, and run from the repo's root with the repo's `.env`
read into the environment, as `uv run --env-file .env` reads it, so a run of the fixture takes no arguments. They print
what the command prints and return the run. An error raises rather than being logged, so a debugger stops where it
happened.

A model's `__main__.py` is the one module of a model that may reach I/O, which it does through this module: workers
import the model, never its `__main__`, so the plugin's import check leaves it out. Its call sits under
`if __name__ == "__main__":`, because each worker process imports the file that started the run."""

import importlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import dotenv_values

from optim import logs, models
from optim import run as runs
from optim.context import open_context
from optim.models import LoadedModel
from optim.settings import Settings
from optim.storage import Root


@dataclass(frozen=True)
class Here:
    """The model whose package holds a file, and where its repo keeps what a run of it needs."""

    repo: Path
    model: LoadedModel
    """Named as the repo's `pyproject.toml` lists it, or by its module path where it is not listed."""
    params: Path
    """Its default params, relative to the repo: `params/MODEL/default.json`, or `params/default.json` beside the
    model, where optim's examples keep theirs."""
    fixture: Path
    """Its fixture, relative to the repo: `tests/MODEL/fixture/`, or `fixture/` beside the model."""


def find(file: str | Path) -> Here:
    """The model whose package holds `file`, such as its `__main__.py`, and its repo: the nearest directory above it
    with a `pyproject.toml`."""
    directory = Path(file).resolve().parent
    parts, package = [], directory
    while (package / "__init__.py").exists():
        parts.insert(0, package.name)
        package = package.parent
    repo = next((parent for parent in directory.parents if (parent / "pyproject.toml").exists()), None)
    if not parts or repo is None:
        msg = f"{file} is not in a model's package in a repo: run the __main__.py beside a model's __init__.py"
        raise LookupError(msg)
    module = ".".join(parts)
    name = next((listed for listed, path in models.listed(repo).items() if path == module), module)
    params = _first(repo / "params" / name / "default.json", directory / "params" / "default.json")
    fixture = _first(repo / "tests" / name / "fixture", directory / "fixture")
    return Here(repo, models.read(name, importlib.import_module(module)), params.relative_to(repo), fixture.relative_to(repo))


def run(
    file: str | Path,
    *,
    params: str | Path | None = None,
    portfolios: Sequence[str] | None = None,
    as_of: date | str | None = None,
    data: str | Path | None = None,
    labels: Mapping[str, str] = runs.NO_LABELS,
    workers: int | None = None,
    cache: bool = True,
) -> runs.Run:
    """Decide portfolios with the model whose package holds `file`, as `optim run` does, and save the run. With neither
    `portfolios` nor `data`, it decides every portfolio of the model's fixture, as of the fixture's date. With
    `portfolios`, it reads each dataset from its source, as the environment configures them, as of `as_of`, a date or
    `YYYY-MM-DD`, or else today in New York; `data` reads each dataset that has a file there instead, as `--data` does.
    `params` defaults to the model's default params. A relative path is the repo's."""
    here = _start(file)
    if portfolios is None and data is None:
        data, as_of = here.fixture, as_of or _fixture_date(here.fixture)
    settings = Settings()
    as_of_date = (as_of if isinstance(as_of, date) else date.fromisoformat(as_of)) if as_of else runs.today()
    with open_context(settings, cache=cache) as ctx:
        ran = runs.run(
            ctx.loader,
            ctx.snapshot_root,
            here.model,
            Path(params) if params else here.params,
            as_of_date=as_of_date,
            portfolio_ids=portfolios,
            data_dir=Path(data) if data else None,
            workers=workers or settings.workers,
            labels=labels,
            orders=ctx.orders,
        )
    sys.stdout.write(ran.summary())
    return ran


def replay(
    file: str | Path, run_id: str, *, params: str | Path | None = None, portfolios: Sequence[str] | None = None, labels: Mapping[str, str] = runs.NO_LABELS, workers: int | None = None
) -> runs.Run:
    """Decide a saved run again with the code you have now, and other params if given, as `optim replay` does, and save
    it as a new run. `run_id` is a run under `OPTIM_SNAPSHOT_ROOT`, or a snapshot's path or URL."""
    _start(file)
    settings = Settings()
    snapshot = Root.at(run_id) if "/" in run_id or "\\" in run_id else Root.at(settings.snapshot_root) / run_id
    ran = runs.replay(snapshot, Root.at(settings.snapshot_root), params_file=Path(params) if params else None, portfolio_ids=portfolios, workers=workers or settings.workers, labels=labels)
    sys.stdout.write(ran.summary())
    return ran


def _start(file: str | Path) -> Here:
    """Find the model, move to its repo's root, read the repo's `.env` into the environment, where a variable already
    set wins, and log as text, for a person, unless the environment says otherwise."""
    here = find(file)
    os.chdir(here.repo)
    if (env := here.repo / ".env").exists():
        for key, value in dotenv_values(env).items():
            if value is not None:
                os.environ.setdefault(key, value)
    elif "OPTIM_SNAPSHOT_ROOT" not in os.environ:
        msg = f"{env} does not exist: copy .env.example to .env, which sets OPTIM_SNAPSHOT_ROOT, all that a run of the fixture needs"
        raise FileNotFoundError(msg)
    os.environ.setdefault("OPTIM_LOG_FORMAT", "text")
    logs.setup()
    return here


def _first(*paths: Path) -> Path:
    """The first of `paths` that exists, or else the first, which an error then names."""
    return next((path for path in paths if path.exists()), paths[0])


def _fixture_date(fixture: Path) -> date:
    return date.fromisoformat(json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))["as_of_date"])
