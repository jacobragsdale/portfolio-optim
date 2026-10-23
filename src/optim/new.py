"""`optim new`: a model repo, or a model in one, from the installed package's examples and templates, so a generated repo
matches the `optim` version that made it. `new_repo` writes the repo, runs `git init` and `uv sync`, and leaves the
first commit to the quant; `new_model` adds a model to the repo it runs in, with its entry point."""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from optim import sync

PACKAGE = Path(__file__).parent
EXAMPLES = ("cvxpy", "plain")
OPTIM = "optim>=0.1,<0.2"  # 0.x: a minor release may break the contract, so a repo takes patches alone until it re-locks
_SKIP = {"__pycache__", "params", "fixture", "tests"}  # copied elsewhere in the repo, or not at all
# Stored under other names, so they act on no repo but the one they are copied into: without their dot, and
# pyproject.toml, which ruff would read as this package's config, as .in.
_STORED = {"gitignore": ".gitignore", "env.example": ".env.example", "python-version": ".python-version", "pyproject.toml.in": "pyproject.toml"}


def new_repo(name: str, *, example: str = "cvxpy", model: str | None = None, directory: Path, index: str | None = None, uv_sync: bool = True) -> Path:
    """A model repo `directory/name`, with one model, `model` or else the repo's name, copied from `example`."""
    repo = directory / name
    if repo.exists() and any(repo.iterdir()):
        msg = f"{repo} exists and is not empty"
        raise FileExistsError(msg)
    model = model or name
    names = {"repo": name, "package": _identifier(name), "model": model, "module": _identifier(model), "optim": OPTIM, "index": _index(index)}
    for template in sorted((PACKAGE / "templates" / "repo").rglob("*")):
        if template.is_file() and "__pycache__" not in template.parts:
            relative = template.relative_to(PACKAGE / "templates" / "repo")
            _write(repo / relative.parent / _STORED.get(relative.name, relative.name), _fill(template.read_text(encoding="utf-8"), names))
    _write(repo / "src" / names["package"] / "__init__.py", f'"""{name}: portfolio models that run on optim."""\n')
    _copy_model(repo, names["package"], model, example)
    sync.sync(repo)
    _run(["git", "init", "--quiet", "--initial-branch=main"], repo)
    if uv_sync:
        _run(["uv", "sync", "--quiet"], repo)
    return repo


def new_model(name: str, *, example: str = "cvxpy", repo: Path, uv_sync: bool = True) -> Path:
    """Add model `name`, copied from `example`, to the model repo at `repo`, with its entry point."""
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        msg = f"{repo} has no pyproject.toml: run optim new model in a model repo, or optim new repo to make one"
        raise FileNotFoundError(msg)
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    package = project.get("tool", {}).get("uv", {}).get("build-backend", {}).get("module-name") or _identifier(project["project"]["name"])
    if name in project["project"].get("entry-points", {}).get("optim.models", {}):
        msg = f"{repo} already has a model named {name}"
        raise FileExistsError(msg)
    directory = _copy_model(repo, package, name, example)
    text = pyproject.read_text(encoding="utf-8")
    header = '[project.entry-points."optim.models"]\n'
    line = f'{name} = "{package}.{_identifier(name)}"\n'
    pyproject.write_text(text.replace(header, header + line) if header in text else f"{text.rstrip()}\n\n{header}{line}", encoding="utf-8", newline="\n")
    if uv_sync:  # the entry point is installed metadata: uv reinstalls the project to list it
        _run(["uv", "sync", "--quiet"], repo)
    return directory


def _copy_model(repo: Path, package: str, model: str, example: str) -> Path:
    """The example's module, rules and README into the package, its params into `params/MODEL/`, and its fixture and
    tests into `tests/MODEL/`, with its imports pointed at the new module."""
    if example not in EXAMPLES:
        msg = f"no example {example!r}: choose one of {list(EXAMPLES)}"
        raise ValueError(msg)
    source, module = PACKAGE / "examples" / example, _identifier(model)
    directory = repo / "src" / package / module
    if directory.exists():
        msg = f"{directory} exists"
        raise FileExistsError(msg)
    rename = {f"optim.examples.{example}": f"{package}.{module}"}
    for file in sorted(source.rglob("*")):
        if not file.is_file() or _SKIP & set(file.relative_to(source).parts):
            continue
        _write(directory / file.relative_to(source), _fill(file.read_text(encoding="utf-8"), {}, rename))
    _write(repo / "params" / model / "default.json", (source / "params" / "default.json").read_text(encoding="utf-8"))
    for file in sorted((source / "fixture").iterdir()):
        _write(repo / "tests" / model / "fixture" / file.name, file.read_text(encoding="utf-8"))
    for file in sorted((source / "tests").glob("*.py")):
        _write(repo / "tests" / model / file.name, _fill(file.read_text(encoding="utf-8"), {}, rename))
    return directory


def _fill(text: str, names: dict[str, str], rename: dict[str, str] | None = None) -> str:
    for old, new in (rename or {}).items():
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    return re.sub(r"\{\{(\w+)\}\}", lambda found: names.get(found.group(1), found.group(0)), text)


def _index(url: str | None) -> str:
    """The internal index, where `optim` is published, as the repo's pyproject says it; empty for uv's own."""
    if url is None:
        return ""
    return f'\n[[tool.uv.index]]\nname = "internal"\nurl = "{url}"\n\n[tool.uv.sources]\noptim = {{ index = "internal" }}\n'


def _identifier(name: str) -> str:
    identifier = re.sub(r"\W", "_", name.replace("-", "_")).lower()
    if not identifier.isidentifier() or identifier[0].isdigit():
        msg = f"{name!r} cannot name a Python package or module: start with a letter, and use letters, digits, - and _"
        raise ValueError(msg)
    return identifier


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _run(command: list[str], cwd: Path) -> None:
    if shutil.which(command[0]) is None:
        msg = f"{command[0]} is not on PATH: run `{' '.join(command)}` in {cwd} once it is"
        raise FileNotFoundError(msg)
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 -- fixed arguments, from PATH, as the quant runs them
