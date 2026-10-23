"""`optim.ide`: running a model by running its `__main__.py`, as from an IDE, which runs it as `optim run` and
`optim replay` do, from the repo's root and with the repo's `.env`."""

import json
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from optim import ide
from optim.new import new_repo

MAIN = Path("src") / "ladder" / "ladder" / "__main__.py"


@pytest.fixture
def environ() -> Generator[None]:
    """The process's environment, restored after the test: running a model's file reads its repo's `.env` into it."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def repo(tmp_path: Path, on_path: list[str], environ: None, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: ARG001 -- environ restores what the run sets
    """A generated repo, `ladder`, whose `.env` saves snapshots under it, and a working directory elsewhere, as an
    IDE's may be. No `OPTIM_*` variable is set but what the `.env` sets."""
    repo = new_repo("ladder", directory=tmp_path, index="https://pypi.firm/simple", uv_sync=False)
    (repo / ".env").write_text("OPTIM_SNAPSHOT_ROOT=snapshots\n")
    on_path.insert(0, str(repo / "src"))
    for name in [name for name in os.environ if name.startswith("OPTIM_")]:
        del os.environ[name]
    monkeypatch.chdir(tmp_path)
    return repo


def test_a_models_file_decides_its_fixture_with_its_default_params(repo: Path) -> None:
    ran = ide.run(repo / MAIN)

    info = json.loads((repo / "snapshots" / ran.run_id / "run.json").read_text())
    assert ran.ok
    assert info["model"] == {"name": "ladder", "module": "ladder.ladder"}
    assert info["params_file"]["path"] == str(Path("params") / "ladder" / "default.json")
    assert info["as_of_date"] == "2026-10-16"


def test_a_models_file_replays_a_saved_run(repo: Path) -> None:
    ran = ide.run(repo / MAIN)

    replayed = ide.replay(repo / MAIN, ran.run_id, portfolios=["P1"])

    assert replayed.ok
    assert [outcome.portfolio_id for outcome in replayed.outcomes] == ["P1"]


def test_given_portfolios_a_models_file_reads_each_dataset_from_its_source(repo: Path) -> None:
    with pytest.raises(LookupError, match="reads from mssql, which is not configured: set OPTIM_MSSQL__"):
        ide.run(repo / MAIN, portfolios=["P1"], as_of="2026-10-16")


def test_a_repo_without_a_env_names_the_file_to_copy(repo: Path) -> None:
    (repo / ".env").unlink()

    with pytest.raises(FileNotFoundError, match=r"copy \.env\.example to \.env"):
        ide.run(repo / MAIN)


def test_a_models_file_runs_as_a_script_with_worker_processes(repo: Path, tmp_path: Path) -> None:
    """As an IDE runs it, `python path/to/__main__.py` from another directory. Each worker imports the file too."""
    (repo / ".env").write_text("OPTIM_SNAPSHOT_ROOT=snapshots\nOPTIM_WORKERS=2\n")
    env = {name: value for name, value in os.environ.items() if not name.startswith("OPTIM_")} | {"PYTHONPATH": str(repo / "src")}

    done = subprocess.run([sys.executable, str(repo / MAIN)], cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603 -- this interpreter

    assert done.returncode == 0, done.stderr
    (snapshot,) = (repo / "snapshots").iterdir()
    assert json.loads((snapshot / "run.json").read_text())["workers"] == 2
    assert done.stdout.count(" accepted ") == 2
