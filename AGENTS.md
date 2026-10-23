# Agent guide

This repo is `optim`, the package that quants' model repos depend on. A model is a module exporting `Params`, `Data`
and `decide`; `optim` loads its data in the parent process, decides each portfolio in worker processes, checks each
decision, and saves a snapshot that replays. [docs/design.md](docs/design.md) is the agreed target and the order of
steps to reach it; it is being implemented, and the other pages in `docs/` still describe the code before it.

## Where things are

A run flows top to bottom through this table. Every module opens with a docstring that says what it holds. All paths
are under `src/optim/`.

| Path | Holds |
|---|---|
| `cli.py` | `optim run`, `replay` and `warm`; exit code 0 only when every portfolio was accepted. |
| `settings.py`, `context.py` | `OPTIM_*` environment variables, and the only place sources and the cache open. |
| `models.py` | Finding a model by its `optim.models` entry point or module path, and reading its hooks. |
| `contract.py` | `optim.Params` (strict), `optim.Datasets` (typed dataset attributes), `optim.Decision`, `optim.Metrics`. |
| `datasets/` | The shared datasets every model reads: `HOLDINGS` and `SECURITIES`, with their SQL. |
| `data/` | `dataset.py` declares a dataset and `frames.py` validates rows, with no I/O; `loader.py` loads a model's datasets in batches and read-only transactions, and `cache.py` caches them in files. |
| `sources/` | Oracle (thin or thick), SQL Server, REST and files: bounded fetches, read-only transactions. |
| `storage.py` | The snapshot and cache roots, a path or URL each, through `pyarrow.fs`, with atomic writes. |
| `run.py` | `run` and `replay`, and `decide_all`, which calls a model's hooks for every portfolio. |
| `engine.py` | One piece of work per portfolio, in this process or a `spawn` process pool. |
| `cvxpy.py` | `optim.cvxpy`: solving a model's cvxpy problem the same way everywhere, and reading it as a `Decision`. |
| `snapshots.py` | The snapshot: `run.json` (`RunInfo`), each dataset as loaded, and the result tables. |
| `testing.py` | Builders for a model's behaviour tests. |
| `examples/` | The `cvxpy` example model, with its fixture, params and tests. |

## Rules to keep

- Model code and workers do no I/O. The parent loads, the workers compute, and the parent saves; replay and snapshots
  depend on it. `lint-imports` enforces it for the core's pure modules and the examples.
- Snapshots hold each dataset as loaded, never a frame derived from one, so replay reruns the model's hooks.
- Every query takes only the as-of date and, for a dataset read per portfolio, the portfolio ids. Params never change
  what is loaded, so a sweep can run every variant from one snapshot.
- A decision may trade only when it is accepted, never on the solver's status alone.
- Every value is a float; rounding to each security's trading increment makes trades exact.
- Say "portfolio", never "account", except in SQL Server's own table and column names.
- Document model fields with a docstring under them, not a comment: it becomes the field's description.

## Checks

```sh
uv run pre-commit run --all-files   # ruff, ty, lockfile, import contracts
uv run pytest                        # the core's tests and the examples'
OPTIM_SNAPSHOT_ROOT=snapshots uv run optim run optim.examples.cvxpy --params src/optim/examples/cvxpy/params/default.json \
  --data src/optim/examples/cvxpy/fixture --as-of 2026-10-16
```

`snapshots/` is not in git, and a snapshot written before a schema change may not load.
