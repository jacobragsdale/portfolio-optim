# Agent guide

A tax-aware optimizer for taxable fixed income portfolios. One run loads data once, solves each portfolio in a worker
process with cvxpy and HiGHS, and saves a snapshot. A strategy JSON file (`strategies/`) says how to optimize. Read
[docs/architecture.md](docs/architecture.md) for why it is built this way, and [docs/extending.md](docs/extending.md)
before adding a dataset, rule, constraint, term, source, job or output.

## Where things are

A run flows top to bottom through this table. Every module opens with a docstring that says what it holds.

| Path | Holds |
|---|---|
| `cli.py` | Arguments to a request; exit code 0 only when every portfolio solved cleanly. |
| `context.py`, `settings.py` | The only place connections open; `OPTIM_*` environment variables. |
| `jobs/` | A request class per job (`optimize`, `model_portfolio`, `transition`) and the solves it makes; `run` and `replay` run any job. |
| `datasets/`, `datasets/sql/` | Every dataset: row schema, source, query, key, cache policy. |
| `data/loader.py`, `data/cache.py` | Fetching, validation, batching and the Redis cache, for every dataset. |
| `inputs.py` | `load_frames` (every dataset a run reads), then prep: `Market` (shared by a run), `Portfolio`, and `SolverInputs` (what one solve sees). |
| `engine.py` | Solving tasks in this process or a process pool. |
| `rules/` | Pre-solve rules: edit `SolverInputs` (what may be bought, expected returns). |
| `optimizer/model.py` | Variables and expressions: buys per bond, sells per tax lot, weights, cash, tax, cost. |
| `optimizer/objective.py`, `optimizer/constraints/` | Objective terms and constraints, built against the model. |
| `optimizer/solve.py`, `rounding.py`, `results.py` | Solve one portfolio, make trades exact, re-check every constraint. |
| `outputs/snapshots.py` | The run's snapshot: request, inputs and results ([docs/snapshots.md](docs/snapshots.md)). Other outputs go in `outputs/`, after it. |
| `strategy.py` | The strategy JSON contract. `uv run optim schema` prints its JSON schema. |

All paths are under `src/optim/`.

## Rules to keep

- Workers do no I/O. The parent loads, the workers compute, and the parent saves; replay and snapshots depend on it.
- Snapshots hold each dataset as loaded, never a frame derived from one, and everything after the loader is pure, so
  replay reruns prep and rules. Logic that combines datasets and every strategy needs is prep, in `inputs.py`; logic a
  strategy chooses is a rule. See [Preparing the inputs](docs/architecture.md#preparing-the-inputs).
- Rules never drop rows. A bond you may not buy can still be held and sold; a held bond without its row fails the solve.
- Constraints and objective terms are in weights (fractions of pre-trade value): divide dollars by `m.value`. The
  re-check after every solve uses one tolerance in weights.

## Checks

```sh
uv run pre-commit run --all-files   # ruff, ty, lockfile
uv run pytest
uv run optim replay snapshots/RUN_ID   # the saved run, with your code; --strategy FILE to try another
```

Replay solves a saved run again and needs no `.env` or database, so it is the way to see a solver change work
end to end. `snapshots/` is not in git, and a snapshot written before a schema change may not load.

## Porting another optimizer

Follow [docs/porting.md](docs/porting.md): inventory the old code in a porting log first, decide for each behaviour
whether to port, change or drop it, then sanity-check outcomes on the same day's data. The old optimizer is a list of
requirements, not results to match trade for trade.
