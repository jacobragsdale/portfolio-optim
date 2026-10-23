# Agent guide

This repo is `optim`, the package that quants' model repos depend on. A model is a module exporting `Params`, `Data`
and `decide`, plus optional hooks; `optim` loads its data in the parent process, decides each portfolio in worker
processes, rounds and checks each decision, and saves a snapshot that replays. [docs/design.md](docs/design.md) is the
design it implements, with its deviations; the docs for quants are in `src/optim/docs/`, and ship with the package.

## Where things are

A run flows top to bottom through this table. Every module opens with a docstring that says what it holds. All paths
are under `src/optim/`.

| Path | Holds |
|---|---|
| `cli.py` | Every `optim` command, one handler each; `run` exits 0 only when every portfolio was accepted. |
| `settings.py`, `context.py` | `OPTIM_*` environment variables, and the only place sources, the cache and the orders table open. |
| `models.py` | Finding a model by its `optim.models` entry point or module path, and reading its hooks. |
| `contract.py` | `optim.Params` (strict), `optim.Datasets` (typed dataset attributes), `optim.Decision`, `optim.Metrics`. |
| `datasets/` | The shared datasets every model reads, `HOLDINGS` and `SECURITIES`, with their SQL. |
| `data/` | `dataset.py` declares a dataset and `frames.py` validates rows, with no I/O; `loader.py` loads a model's datasets in batches and read-only transactions, and `cache.py` caches them in files. |
| `sources/` | Oracle (thin or thick), SQL Server, REST and files: bounded fetches, read-only transactions. |
| `storage.py` | The snapshot and cache roots, a path or URL each, through `pyarrow.fs`, with atomic writes. |
| `run.py` | `run`, `replay` and `open_snapshot`. |
| `ide.py` | `optim.ide`: running a model by running its `__main__.py`, as from an IDE, through `run` and `replay`, from the repo's root with its `.env`. |
| `decide.py` | `decide_all`, which calls a model's hooks for every portfolio, at once or in turn when the model reserves. |
| `engine.py` | One piece of work per portfolio, in this process or a `spawn` process pool. |
| `acceptance.py` | Rounding each decision to its securities' increments, and the core's checks of whether it may trade. |
| `cvxpy.py` | `optim.cvxpy`: solving a model's cvxpy problem the same way everywhere, and reading it as a `Decision`. |
| `snapshots.py` | The snapshot: `run.json` (`RunInfo`), each dataset as loaded, and the result tables. |
| `orders.py` | Writing accepted portfolios' orders to the orders table, where the environment configures one. |
| `research.py` | `sweep`, `regress`, `describe`, and `trial`, which decides one portfolio of a snapshot from Python. |
| `testing.py` | Builders for a model's behaviour tests. |
| `pytest_plugin.py` | The `pytest11` plugin: contract tests for every model a repo lists, the import-graph check, the agent-files check, and the e2e suite's `optim_qa` fixture and safeguards. |
| `sync.py`, `templates/contract.md`, `skills/` | The agent files `optim sync` writes into a model repo: the contract block in `AGENTS.md` and the `optim-*` skills. |
| `new.py`, `templates/repo/` | `optim new repo` and `optim new model`: a model repo, or a model in one, from the examples and templates. |
| `examples/` | The `cvxpy` and `plain` examples, with their fixtures, params and tests, which `optim new` copies. Their `ruff.toml` sorts imports as a model repo does. |
| `docs/` | The docs for quants: one tutorial, the how-to guides, the reference and the explanation. `optim docs` prints them. |

## Rules to keep

- Model code and workers do no I/O. The parent loads, the workers compute, and the parent saves; replay and snapshots
  depend on it. `lint-imports` enforces it for the core's pure modules and the examples, and the plugin's import check
  enforces it in model repos.
- A model's `__main__.py` is the one module of a model that may reach I/O, through `optim.ide`: workers never import
  it, so the plugin's import check and the core's import contract leave it out. Nothing else may import `optim.ide`.
- Snapshots hold each dataset as loaded, never a frame derived from one, so replay reruns the model's hooks.
- Every query takes only the as-of date and, for a dataset read per portfolio, the portfolio ids. Params never change
  what is loaded, so a sweep can run every variant from one snapshot.
- A decision may trade only when it is accepted, never on the solver's status alone.
- Every value is a float; rounding to each security's trading increment makes trades exact.
- A field added to `RunInfo` gets a default, so snapshots from older versions still load.
- The examples, templates and skills are what every new model repo starts from: after changing one, generate a repo
  and run its own checks (see Checks).
- Say "portfolio", never "account", except in SQL Server's own table and column names.
- Document model fields with a docstring under them, not a comment: it becomes the field's description.

## Changing the core

- A shared dataset is a row model and a `Dataset` in `datasets/`, with its SQL in `datasets/sql/`. Add one only when two
  models read it.
- A source is a module in `sources/` with a settings model and a `fetch(query, params)` and `read_only()`, a name in
  `SourceName`, a field in `Settings`, a line in `open_context`, and a line in `qa_problems` in `pytest_plugin.py`, so
  the e2e suite checks it for the QA marker. Add its module, and its driver, to the import contract in `pyproject.toml`
  and to the plugin's `FORBIDDEN`. Bound every call with a deadline and `max_rows`.
- A solver is a reader in `optim.cvxpy._READERS` and a name in `SolveSettings.solver`.
- A command is a handler in `cli.py`, registered with `_command`.
- A docs page is a Markdown file in `docs/`, and a link from the README.

## Checks

```sh
uv run pre-commit run --all-files   # ruff, ty, lockfile, import contracts
uv run pytest                        # the core's tests and the examples'
```

To check what `optim new` generates, make a repo in a scratch directory that takes `optim` from this checkout, then run
its checks and its model's `__main__.py`:

```sh
uv run optim new repo ladder --dir SCRATCH --optim-path .
cd SCRATCH/ladder
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run ty check
cp .env.example .env && uv run python -m ladder.ladder
```

`snapshots/` is not in git, and a snapshot written before a schema change may not load.
