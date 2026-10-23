"""optim's pytest plugin, which registers itself when `optim` is installed. In a model repo, it adds contract tests for
every model the repo's `pyproject.toml` lists, whenever pytest runs without paths: the model's default params validate,
`decide` runs on its fixture and every portfolio is accepted, its modules cannot reach I/O through their imports, and
the agent files match the installed `optim`. No repo carries import-linter configuration that could drift.

For the repo's e2e suite, marked `e2e`, the `optim_qa` fixture runs `optim run` against the QA environment. It refuses
to run unless every configured source and destination carries a QA marker (`optim_qa_markers`, default `qa`) and every
write goes to the allowlist (`optim_e2e_writes`: the QA snapshot root and orders table), and `QA.portfolios` picks test
portfolios by what they hold, with a read-only query."""

import importlib
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Literal, override

import grimp
import pandas as pd
import pytest

from optim import models, sync
from optim.context import open_context
from optim.contract import declared
from optim.data.loader import Loader, load_data
from optim.decide import decide_all
from optim.models import LoadedModel
from optim.run import from_dir, read_params
from optim.settings import Settings
from optim.snapshots import read_results
from optim.sources import Param, SourceName
from optim.sources.file import FileSource
from optim.sources.mssql import target
from optim.storage import Root

FORBIDDEN = (
    "optim.sources.sql", "optim.sources.oracle", "optim.sources.mssql", "optim.sources.rest", "optim.sources.file",
    "optim.data.loader", "optim.data.cache", "optim.storage", "optim.snapshots", "optim.orders", "optim.run",
    "optim.research", "optim.context", "optim.settings", "optim.cli", "oracledb", "mssql_python", "pyodbc", "pymssql",
    "cx_Oracle", "sqlalchemy", "psycopg", "psycopg2", "snowflake", "httpx", "requests", "urllib3", "aiohttp",
)  # fmt: skip
"""What a model's modules may not reach through their imports: a source, the loader, the cache, outputs, a database
driver or an HTTP client."""


type TracebackStyle = Literal["long", "short", "line", "no", "native", "value", "auto"]  # pytest's --tb styles


class ContractError(AssertionError):
    """A model breaks the contract; the message says how, and what to do."""


def listed(repo: Path) -> dict[str, str]:
    """The models a repo's `pyproject.toml` lists, by name: their module paths."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(pyproject.get("project", {}).get("entry-points", {}).get(models.GROUP, {}))


def check_params(model: LoadedModel, params: Path) -> None:
    if not params.exists():
        msg = f"{model.name} has no default params: add {params}, which every model's repo keeps"
        raise ContractError(msg)
    read_params(model, params)


def check_fixture(model: LoadedModel, fixture: Path, params: Path) -> None:
    """Load every dataset from the fixture, decide every portfolio in its holdings with the default params, and require
    each to be accepted, reporting only the metrics its `Metrics` declares."""
    if not (fixture / "fixture.json").exists():
        msg = f"{model.name} has no fixture: add {fixture}, a file per dataset and fixture.json with its as_of_date"
        raise ContractError(msg)
    datasets = {attribute: from_dir(dataset, fixture) for attribute, dataset in declared(model.data).items()}
    if missing := sorted(dataset.name for dataset in datasets.values() if dataset.source != "file"):
        msg = f"{model.name}'s fixture {fixture} has no file for {missing}: add NAME.csv or NAME.parquet for each"
        raise ContractError(msg)
    as_of_date = date.fromisoformat(json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))["as_of_date"])
    holdings = datasets["holdings"].query
    portfolio_ids = list(dict.fromkeys(pd.read_csv(holdings, dtype=str)["portfolio_id"] if str(holdings).endswith(".csv") else pd.read_parquet(holdings)["portfolio_id"]))
    frames, _ = load_data(Loader({"file": FileSource()}), datasets, as_of_date, portfolio_ids)
    outcomes = decide_all(model, read_params(model, params)[0], model.data(frames, as_of_date=as_of_date), portfolio_ids, workers=1).outcomes
    if rejected := [f"{outcome.portfolio_id}: {'; '.join(outcome.reasons)}" for outcome in outcomes if not outcome.accepted]:
        msg = f"{model.name} rejects portfolios of its fixture {fixture}:\n" + "\n".join(rejected)
        raise ContractError(msg)
    declared_metrics = set(model.metrics.model_fields) if model.metrics else set()
    if wrong := sorted({name for outcome in outcomes if outcome.decision for name in outcome.decision.metrics} ^ declared_metrics):
        msg = f"{model.name}'s decisions report metrics its Metrics does not declare, or declares metrics they do not report: {wrong}"
        raise ContractError(msg)


def check_imports(model: LoadedModel) -> None:
    """No module of the model, or one it imports, can reach what `FORBIDDEN` lists."""
    graph = _graph(model.module.partition(".")[0])
    chains = [chain for forbidden in FORBIDDEN if forbidden in graph.modules for chain in graph.find_shortest_chains(importer=model.module, imported=forbidden, as_packages=True)]
    if chains:
        msg = f"{model.name} can reach I/O through its imports, which model code must not do: data arrives in its Data.\n" + "\n".join(" -> ".join(chain) for chain in sorted(chains))
        raise ContractError(msg)


@cache
def _graph(package: str) -> grimp.ImportGraph:
    return grimp.build_graph(package, "optim", include_external_packages=True)


def check_agent_files(repo: Path) -> None:
    if stale := sync.stale(repo):
        msg = f"the agent files do not match the installed optim: run `uv run optim sync`, which rewrites {', '.join(map(str, stale))}"
        raise ContractError(msg)


class ContractItem(pytest.Item):
    def __init__(self, *, check: Callable[[], None], **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type] -- pytest's from_parent passes its own
        self.check = check

    @override
    def runtest(self) -> None:
        self.check()

    @override
    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: TracebackStyle | None = None) -> str:
        return str(excinfo.value) if isinstance(excinfo.value, ContractError) else str(super().repr_failure(excinfo, style=style))

    @override
    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"optim contract: {self.name}"


class ModelRepo(pytest.File):
    """The repo's `pyproject.toml`, collected as its models' contract tests."""

    @override
    def collect(self) -> Iterator[ContractItem]:
        repo = self.path.parent
        for name, module in listed(repo).items():
            model = models.read(name, importlib.import_module(module))
            yield ContractItem.from_parent(self, name=f"params[{name}]", check=lambda model=model, name=name: check_params(model, repo / "params" / name / "default.json"))
            yield ContractItem.from_parent(
                self, name=f"fixture[{name}]", check=lambda model=model, name=name: check_fixture(model, repo / "tests" / name / "fixture", repo / "params" / name / "default.json")
            )
            yield ContractItem.from_parent(self, name=f"imports[{name}]", check=lambda model=model: check_imports(model))
        if listed(repo):
            yield ContractItem.from_parent(self, name="agent-files", check=lambda: check_agent_files(repo))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("optim_qa_markers", "text every QA source and destination contains, such as qa", type="linelist", default=["qa"])
    parser.addini("optim_e2e_writes", "the only snapshot roots, cache roots and orders tables the e2e suite may write to", type="linelist", default=[])
    parser.addini("optim_e2e_command", "the command that runs the deployed optim (default: this interpreter's)", type="args", default=[sys.executable, "-m", "optim"])


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: runs the deployed optim against the QA environment; refuses to run anywhere else")
    _graph.cache_clear()  # one graph per session: the code may change between sessions in one process


@pytest.hookimpl(tryfirst=True)  # before -k and -m select among the items
def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    pyproject = config.rootpath / "pyproject.toml"
    if config.option.file_or_dir or not pyproject.exists() or not listed(config.rootpath):  # a run of chosen tests, or not a model repo
        return
    items[:0] = list(ModelRepo.from_parent(session, path=pyproject).collect())


def qa_problems(settings: Settings, *, markers: Sequence[str], writes: Sequence[str]) -> list[str]:
    """Why the environment is not safe for the e2e suite: a configured source or destination without a QA marker, or
    a write outside the allowlist. None when it is."""
    named = {"OPTIM_SNAPSHOT_ROOT": settings.snapshot_root}
    named |= {"OPTIM_CACHE_ROOT": settings.cache_root} if settings.cache_root else {}
    named |= {"OPTIM_ORACLE__DSN": settings.oracle.dsn} if settings.oracle else {}
    named |= {"OPTIM_MSSQL__CONNECTION_STRING": target(settings.mssql.connection_string.get_secret_value())} if settings.mssql else {}
    named |= {"OPTIM_REST__URL": str(settings.rest.url)} if settings.rest else {}
    if settings.orders:
        named |= {"OPTIM_ORDERS__CONNECTION_STRING": target(settings.orders.connection_string.get_secret_value()), "OPTIM_ORDERS__TABLE": settings.orders.table}
    problems = [f"{name} ({value}) contains none of the QA markers {list(markers)}" for name, value in named.items() if not any(m.lower() in value.lower() for m in markers)]
    written = {name: value for name, value in named.items() if name in ("OPTIM_SNAPSHOT_ROOT", "OPTIM_CACHE_ROOT", "OPTIM_ORDERS__TABLE")}
    problems += [f"{name} ({value}) is not in optim_e2e_writes {list(writes)}" for name, value in written.items() if value not in writes]
    return problems


_READ_ONLY = re.compile(r"^\s*(--[^\n]*\n\s*|/\*.*?\*/\s*)*(SELECT|WITH)\b", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class E2ERun:
    run_id: str
    exit_code: int
    snapshot: Root


@dataclass(frozen=True)
class QA:
    """The QA environment, checked safe: what the e2e suite runs `optim` against."""

    settings: Settings
    command: Sequence[str]
    root: Path
    """The repo, where the command runs, so params paths relative to it resolve in an image whose workdir holds it."""

    def portfolios(self, query: Path, *, source: SourceName = "mssql", **params: Param) -> list[str]:
        """Portfolio ids, picked by what they hold with a SELECT in a SQL file that returns `portfolio_id`, read in a
        read-only transaction and rolled back, rather than by fixed ids that drift."""
        if not _READ_ONLY.match(query.read_text(encoding="utf-8")):
            msg = f"{query} is not a SELECT: the e2e suite only reads"
            raise ValueError(msg)
        with open_context(self.settings) as ctx, ctx.loader.read_only(source) as scoped:
            columns = scoped.sources[source].fetch(str(query), params)
        return list(dict.fromkeys(str(portfolio_id) for portfolio_id in columns["portfolio_id"]))

    def run(self, model: str, params: Path, portfolios: Sequence[str], *args: str) -> E2ERun:
        """`optim run` as the deployed image runs it, labelled `purpose=e2e`, from the repo: `params` is best relative
        to it, as `params/MODEL/default.json`."""
        command = [*self.command, "run", model, "--params", params.as_posix(), "--portfolios", *portfolios, "--label", "purpose=e2e", *args]
        ran = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=False)  # noqa: S603 -- the configured command
        if not (found := re.search(r"^run (\S+)$", ran.stdout, re.MULTILINE)):
            msg = f"optim run did not complete (exit {ran.returncode}):\n{ran.stderr[-4000:]}"
            raise AssertionError(msg)
        return E2ERun(found.group(1), ran.returncode, Root.at(self.settings.snapshot_root) / found.group(1))

    def replay(self, run: E2ERun) -> E2ERun:
        ran = subprocess.run([*self.command, "replay", run.run_id, "--label", "purpose=e2e"], cwd=self.root, capture_output=True, text=True, check=False)  # noqa: S603 -- the configured command
        if not (found := re.search(r"^run (\S+)$", ran.stdout, re.MULTILINE)):
            msg = f"optim replay did not complete (exit {ran.returncode}):\n{ran.stderr[-4000:]}"
            raise AssertionError(msg)
        return E2ERun(found.group(1), ran.returncode, Root.at(self.settings.snapshot_root) / found.group(1))

    def smoke(self, model: str, params: Path, portfolios: Sequence[str], *args: str) -> E2ERun:
        """Every model's smoke case: the run completes, each portfolio is accepted or rejected with reasons, and its
        replay gives the same trades for the solves that did not stop at a time limit."""
        ran = self.run(model, params, portfolios, *args)
        results = read_results(ran.snapshot)
        decisions = results["decisions"]
        if set(decisions["portfolio_id"]) != set(portfolios):
            msg = f"the run decided {sorted(decisions['portfolio_id'])}, not {sorted(portfolios)}"
            raise AssertionError(msg)
        if silent := set(decisions.loc[decisions["status"] == "rejected", "portfolio_id"]) - set(results["reasons"]["portfolio_id"]):
            msg = f"rejected without reasons: {sorted(silent)}"
            raise AssertionError(msg)
        timed_out = set(decisions.loc[decisions["stop"] == "limit", "portfolio_id"])
        again = read_results(self.replay(ran).snapshot)["trades"]
        key = ["portfolio_id", "security_id", "side", "quantity"]
        before, after = (frame.loc[~frame["portfolio_id"].isin(timed_out), key].sort_values(key).reset_index(drop=True) for frame in (results["trades"], again))
        pd.testing.assert_frame_equal(before, after, obj="trades, run then replayed")
        return ran


@pytest.fixture(scope="session")
def optim_qa(pytestconfig: pytest.Config) -> QA:
    """The QA environment, from `OPTIM_*` settings, or a failure that says why it is not safe to run against."""
    settings = Settings()
    markers, writes = pytestconfig.getini("optim_qa_markers"), pytestconfig.getini("optim_e2e_writes")
    if problems := qa_problems(settings, markers=markers, writes=writes):
        pytest.fail("refusing to run the e2e suite outside QA:\n" + "\n".join(problems), pytrace=False)
    return QA(settings, pytestconfig.getini("optim_e2e_command"), pytestconfig.rootpath)
