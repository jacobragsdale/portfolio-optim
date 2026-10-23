"""`optim new`: a model repo, or another model in one, from the installed examples and templates, whose own contract
tests pass as generated."""

import tomllib
from pathlib import Path

import pytest

from optim import models, sync
from optim.new import OPTIM, new_model, new_repo
from optim.pytest_plugin import check_agent_files, check_fixture, check_imports, check_params, listed

INDEX = "https://pypi.firm/simple"
CHECKOUT = Path(__file__).parents[1]


def test_a_new_repo_holds_one_model_and_everything_around_it(tmp_path: Path) -> None:
    repo = new_repo("ladder-sma", example="cvxpy", model="real-optim", directory=tmp_path, index=INDEX, uv_sync=False)

    project = tomllib.loads((repo / "pyproject.toml").read_text())
    assert project["project"]["dependencies"] == [OPTIM]  # the version range from the index
    assert project["project"]["entry-points"]["optim.models"] == {"real-optim": "ladder_sma.real_optim"}
    for path in ("src/ladder_sma/__init__.py", "src/ladder_sma/real_optim/__init__.py", "src/ladder_sma/real_optim/rules.py", "src/ladder_sma/real_optim/README.md"):
        assert (repo / path).is_file(), path
    for path in ("params/real-optim/default.json", "tests/real-optim/fixture/fixture.json", "tests/real-optim/test_rules.py", "tests/e2e/test_smoke.py"):
        assert (repo / path).is_file(), path
    for path in ("Dockerfile", ".dockerignore", "azure-pipelines.yml", ".gitignore", ".env.example", ".python-version", "README.md", "AGENTS.md", "CLAUDE.md", ".git"):
        assert (repo / path).exists(), path
    code = "".join(path.read_text() for path in (repo / "src").rglob("*.py")) + (repo / "tests" / "real-optim" / "test_rules.py").read_text()
    assert "optim.examples" not in code  # imports point at the repo's own module
    assert "{{" not in (repo / "README.md").read_text() + (repo / "pyproject.toml").read_text()
    assert sync.stale(repo) == []
    assert not any(path.is_symlink() for path in repo.rglob("*"))  # copies, which Git on Windows checks out as files


def test_a_new_repo_takes_optim_from_the_internal_index_or_a_checkout(tmp_path: Path) -> None:
    indexed = tomllib.loads((new_repo("ladder", directory=tmp_path, index=INDEX, uv_sync=False) / "pyproject.toml").read_text())
    checked_out = tomllib.loads((new_repo("ladder", directory=tmp_path / "dev", optim_path=CHECKOUT, uv_sync=False) / "pyproject.toml").read_text())

    assert indexed["tool"]["uv"]["index"] == [{"name": "internal", "url": INDEX}]
    assert indexed["tool"]["uv"]["sources"] == {"optim": {"index": "internal"}}
    assert checked_out["tool"]["uv"]["sources"] == {"optim": {"path": CHECKOUT.resolve().as_posix(), "editable": True}}


@pytest.mark.parametrize("sources", [{}, {"index": INDEX, "optim_path": CHECKOUT}], ids=["neither", "both"])
def test_a_new_repo_that_does_not_say_where_optim_comes_from_is_refused(tmp_path: Path, sources: dict[str, object]) -> None:
    """PyPI has an unrelated package named `optim`, which a repo with no source for optim would install."""
    with pytest.raises(ValueError, match="PyPI"):
        new_repo("ladder", directory=tmp_path, uv_sync=False, **sources)  # ty: ignore[invalid-argument-type] -- each case's keywords

    assert not (tmp_path / "ladder").exists()


def test_a_generated_repos_models_pass_their_contract_tests(tmp_path: Path, on_path: list[str]) -> None:
    repo = new_repo("ladder", example="cvxpy", directory=tmp_path, index=INDEX, uv_sync=False)
    new_model("model-portfolio", example="plain", repo=repo, uv_sync=False)
    on_path.insert(0, str(repo / "src"))

    assert listed(repo) == {"ladder": "ladder.ladder", "model-portfolio": "ladder.model_portfolio"}
    for name, module in listed(repo).items():
        model = models.load(module)
        check_params(model, repo / "params" / name / "default.json")
        check_fixture(model, repo / "tests" / name / "fixture", repo / "params" / name / "default.json")
        check_imports(model)
    check_agent_files(repo)


def test_a_new_model_is_listed_in_the_repos_readme_whatever_columns_its_table_has(tmp_path: Path) -> None:
    repo = new_repo("ladder", directory=tmp_path, index=INDEX, uv_sync=False)
    readme = repo / "README.md"
    readme.write_text(readme.read_text().replace("| Model | Module |\n|---|---|", "| Model | Module | What it does |\n|---|---|---|"))

    new_model("model-portfolio", example="plain", repo=repo, uv_sync=False)

    assert "| `ladder` | `src/ladder/ladder/` |\n| `model-portfolio` | `src/ladder/model_portfolio/` | |\n" in readme.read_text()


def test_a_model_name_that_is_taken_or_cannot_name_a_module_is_refused(tmp_path: Path) -> None:
    repo = new_repo("ladder", directory=tmp_path, index=INDEX, uv_sync=False)

    with pytest.raises(FileExistsError, match="already has a model named ladder"):
        new_model("ladder", repo=repo, uv_sync=False)
    with pytest.raises(ValueError, match="cannot name a Python package"):
        new_model("2fast", repo=repo, uv_sync=False)
