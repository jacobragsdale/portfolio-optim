"""`optim new`: a model repo, or another model in one, from the installed examples and templates, whose own contract
tests pass as generated."""

import sys
import tomllib
from collections.abc import Generator
from pathlib import Path

import pytest

from optim import models, sync
from optim.new import OPTIM, new_model, new_repo
from optim.pytest_plugin import check_agent_files, check_fixture, check_imports, check_params, listed


@pytest.fixture
def on_path(monkeypatch: pytest.MonkeyPatch) -> Generator[list[str]]:
    """`sys.path`, restored after the test, with the modules a generated repo's imports leave behind dropped."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = set(sys.modules)
    yield sys.path
    for module in set(sys.modules) - before:
        del sys.modules[module]


def test_a_new_repo_holds_one_model_and_everything_around_it(tmp_path: Path) -> None:
    repo = new_repo("ladder-sma", example="cvxpy", model="real-optim", directory=tmp_path, uv_sync=False)

    project = tomllib.loads((repo / "pyproject.toml").read_text())
    assert project["project"]["dependencies"] == [OPTIM]  # the version range from the index
    assert project["project"]["entry-points"]["optim.models"] == {"real-optim": "ladder_sma.real_optim"}
    assert "index" not in project["tool"]["uv"]  # uv's own index unless one is given
    for path in ("src/ladder_sma/__init__.py", "src/ladder_sma/real_optim/__init__.py", "src/ladder_sma/real_optim/rules.py", "src/ladder_sma/real_optim/README.md"):
        assert (repo / path).is_file(), path
    for path in ("params/real-optim/default.json", "tests/real-optim/fixture/fixture.json", "tests/real-optim/test_rules.py", "tests/e2e/test_smoke.py"):
        assert (repo / path).is_file(), path
    for path in ("Dockerfile", "azure-pipelines.yml", ".gitignore", ".env.example", ".python-version", "README.md", "AGENTS.md", "CLAUDE.md", ".git"):
        assert (repo / path).exists(), path
    code = "".join(path.read_text() for path in (repo / "src").rglob("*.py")) + (repo / "tests" / "real-optim" / "test_rules.py").read_text()
    assert "optim.examples" not in code  # imports point at the repo's own module
    assert "{{" not in (repo / "README.md").read_text() + (repo / "pyproject.toml").read_text()
    assert sync.stale(repo) == []
    assert not any(path.is_symlink() for path in repo.rglob("*"))  # copies, which Git on Windows checks out as files


def test_a_new_repo_names_the_internal_index_when_given_one(tmp_path: Path) -> None:
    repo = new_repo("ladder", directory=tmp_path, index="https://pypi.firm/simple", uv_sync=False)

    project = tomllib.loads((repo / "pyproject.toml").read_text())
    assert project["tool"]["uv"]["index"] == [{"name": "internal", "url": "https://pypi.firm/simple"}]
    assert project["tool"]["uv"]["sources"] == {"optim": {"index": "internal"}}


def test_a_generated_repos_models_pass_their_contract_tests(tmp_path: Path, on_path: list[str]) -> None:
    repo = new_repo("ladder", example="cvxpy", directory=tmp_path, uv_sync=False)
    new_model("model-portfolio", example="plain", repo=repo, uv_sync=False)
    on_path.insert(0, str(repo / "src"))

    assert listed(repo) == {"ladder": "ladder.ladder", "model-portfolio": "ladder.model_portfolio"}
    for name, module in listed(repo).items():
        model = models.load(module)
        check_params(model, repo / "params" / name / "default.json")
        check_fixture(model, repo / "tests" / name / "fixture", repo / "params" / name / "default.json")
        check_imports(model)
    check_agent_files(repo)


def test_a_model_name_that_is_taken_or_cannot_name_a_module_is_refused(tmp_path: Path) -> None:
    repo = new_repo("ladder", directory=tmp_path, uv_sync=False)

    with pytest.raises(FileExistsError, match="already has a model named ladder"):
        new_model("ladder", repo=repo, uv_sync=False)
    with pytest.raises(ValueError, match="cannot name a Python package"):
        new_model("2fast", repo=repo, uv_sync=False)
