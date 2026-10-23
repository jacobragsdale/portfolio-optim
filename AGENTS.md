# Agent guide

An optimizer for taxable fixed income SMA portfolios: "taxable" describes the mandates, and the optimizer trades
holdings by security, not tax lots. One run loads data once, solves each portfolio, all its sleeves together, in a
worker process with cvxpy and HiGHS or COPT, reviews each result for acceptance, and saves a snapshot. A strategy JSON
file (`strategies/`) says how to optimize. The datasets and business logic are placeholders until an existing optimizer
is ported in: see [Porting another optimizer](#porting-another-optimizer). Read [docs/architecture.md](docs/architecture.md) for why it
is built this way, and [docs/extending.md](docs/extending.md) before adding a dataset, rule, constraint, term, source,
job or output. The repo is becoming the `optim` package that model repos depend on: [docs/design.md](docs/design.md) is
the agreed target and the order of steps to reach it, while this guide and the other docs describe the code as it
stands.

## Where things are

A run flows top to bottom through this table. Every module opens with a docstring that says what it holds.

| Path | Holds |
|---|---|
| `cli.py` | Arguments to a request; exit code 0 only when every portfolio's result was accepted. |
| `context.py`, `settings.py` | The only place connections open; `OPTIM_*` environment variables. |
| `jobs/` | A request class per job, such as `optimize`, and the solves it makes, collected in `Job`; `run.py`: `run` and `replay` run any job, and `RunInfo` is the snapshot's `run.json`. |
| `datasets/`, `datasets/sql/` | Every dataset: row schema, source, query, key, cache policy. Every number is a float. |
| `sources/` | Oracle (thin or thick), SQL Server and REST: bounded fetches, read-only transactions. |
| `data/` | `dataset.py` declares a dataset and `frames.py` validates rows, with no I/O; `loader.py` and `cache.py` fetch, scope reads in read-only transactions, and cache in Redis. |
| `datasets/load.py` | `load_frames`: every dataset a run reads (`MARKET`, `PER_PORTFOLIO` in `datasets/__init__.py`), in batches. |
| `prep/` | Pure prep: the `Market` a run shares (`market.py`); each `Portfolio`, and the `SolverInputs` one solve sees: lines, cash, cash flows (`portfolios.py`, the types in `__init__.py`), with the portfolio's controls applied (`controls.py`). |
| `engine.py` | Solving tasks in this process or a process pool. |
| `rules/` | Pre-solve rules: edit `SolverInputs` lines (what may be bought or sold, expected returns). |
| `optimizer/model.py` | Variables and expressions: buys and sales per line (sleeve and security), weights per scope, exposures, cash, cost. |
| `optimizer/objective/`, `optimizer/constraints/` | Objective terms (linear or quadratic) and constraints, built against the model, for the portfolio or one sleeve. |
| `optimizer/solve.py`, `backends.py`, `rounding.py` | Solve one portfolio with HiGHS or COPT, read why the solver stopped, make trades exact. |
| `optimizer/acceptance.py`, `results.py` | Rebuild the result from its exact trades and check whether it may trade; what a solve returns. |
| `outputs/snapshots.py` | The run's snapshot: request, inputs and results ([docs/snapshots.md](docs/snapshots.md)). Other outputs go in `outputs/`, after it. |
| `strategy.py` | The strategy JSON contract. `uv run optim schema` prints its JSON schema. |

All paths are under `src/optim/`.

## Rules to keep

- Workers do no I/O. The parent loads, the workers compute, and the parent saves; replay and snapshots depend on it.
  `lint-imports` enforces it: `prep`, `rules`, `optimizer`, `strategy` and `engine` may not import the loader, a
  source, the cache, jobs or outputs.
- Snapshots hold each dataset as loaded, never a frame derived from one, and everything after the loader is pure, so
  replay reruns prep and rules. Logic that combines datasets and every strategy needs is prep, in `prep/`; logic a
  strategy chooses is a rule. See [Preparing the inputs](docs/architecture.md#preparing-the-inputs).
- Rules never drop rows: they narrow `buyable` or `sellable` on `inputs.lines`, or edit `expected_return`. A security
  you may not buy can still be held and sold; a held security without its row fails the solve.
- A portfolio's controls are prep, never rules: they apply whatever the strategy, a rule cannot block a mandatory sale,
  and a control that cannot be applied fails the portfolio instead of being skipped.
- Constraints and objective terms are in weights of their scope's base value: divide dollars by `scope.base`, or
  `m.base` for the whole portfolio. Acceptance uses one tolerance in weights.
- A result may trade only when acceptance accepts it, never on the solver's status alone. Never make a strategy
  solvable by switching its solver or replacing a quadratic term with a linear one: refuse it in `Strategy`.
- Document model fields with a docstring under them, not a comment: it becomes the field's description.

## Checks

```sh
uv run pre-commit run --all-files   # ruff, ty, lockfile, import contracts
uv run pytest
uv run optim replay snapshots/RUN_ID   # the saved run, with your code; --strategy FILE to try another
```

Replay solves a saved run again and needs no `.env` or database, so it is the way to see a solver change work
end to end. `snapshots/` is not in git, and a snapshot written before a schema change may not load.

## Porting another optimizer

Follow [docs/porting.md](docs/porting.md): inventory the old code in a porting log first, decide for each behaviour
whether to port, change or drop it, then sanity-check outcomes on the same day's data. The old optimizer is a list of
requirements, not results to match trade for trade. `grep -rn "TODO(port)" src tests` lists every place the port
writes code; remove each marker as the port fills it.
