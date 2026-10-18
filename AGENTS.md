# Agent guide

A tax-aware optimizer for taxable fixed income portfolios. One run loads data once, solves each portfolio in a worker
process with cvxpy and HiGHS, and saves a snapshot. A strategy JSON file (`strategies/`) says how to optimize. Read
[docs/architecture.md](docs/architecture.md) for why it is built this way, and [docs/extending.md](docs/extending.md)
before adding a dataset, rule, constraint, term, source, entry point or output.

## Where things are

A run flows top to bottom through this table. Every module opens with a docstring that says what it holds.

| Path | Holds |
|---|---|
| `cli.py` | Arguments to a request; exit code 0 only when every portfolio solved cleanly. |
| `context.py`, `settings.py` | The only place connections open; `OPTIM_*` environment variables. |
| `jobs/` | One entry point per module: `optimize`, `model_portfolio`, `transition`, `replay`. Each is load, solve, save. |
| `datasets/`, `datasets/sql/` | Every dataset: row schema, source, query, key, cache policy. |
| `data/loader.py` | Fetching, validation, batching and the Redis cache, for every dataset. |
| `inputs.py` | `Market` (shared by a run), `Portfolio`, and `Inputs` (what one solve sees). |
| `engine.py` | Solving tasks in this process or a process pool. |
| `rules/` | Pre-solve rules: edit `Inputs` (what may be bought, expected returns). |
| `optimizer/model.py` | Variables and expressions: buys per bond, sells per tax lot, weights, cash, tax, cost. |
| `optimizer/objective.py`, `optimizer/constraints/` | Objective terms and constraints, built against the model. |
| `optimizer/solve.py`, `rounding.py`, `results.py` | Solve one portfolio, make trades exact, re-check every constraint. |
| `outputs/snapshots.py` | The run's snapshot: request, inputs and results. Other outputs go in `outputs/`, after it. |
| `strategy.py` | The strategy JSON contract. `uv run optim schema` prints its JSON schema. |

All paths are under `src/optim/`.

## Rules to keep

- Workers do no I/O. The parent loads, the workers compute, and the parent saves.
- Rules never drop rows. A bond you may not buy can still be held and sold.
- Constraints and objective terms are in weights (fractions of pre-trade value). Divide dollars by `m.value`.
- A new rule, constraint or term must be added to its union (`Rule`, `Constraint`, `Term`), or strategy JSON rejects it.
- Say "portfolio". "Account" appears only in SQL Server column and table names.
- `Strategy` and its parts use the strict pydantic base (`Strict`); everything else uses the lax `Model` or `Row`.
- Row field rules go in the annotation (`Annotated[float, Field(ge=0)]`). The loader refuses `@field_validator`.
- Never submit orders. There is no order management system (OMS) integration.
- Mark a deliberate shortcut with a `ponytail:` comment that names its limit and the upgrade path.

## Checks

```sh
uv run pre-commit run --all-files   # ruff, ty, lockfile
uv run pytest
uv run optim replay snapshots/RUN_ID --strategy strategies/core.json
```

Replay solves a saved run again and needs no `.env` or database, so it is the way to see a solver change work
end to end. `snapshots/` is not in git, and a snapshot written before a schema change may not load.

## Porting another optimizer

Follow [docs/porting.md](docs/porting.md): inventory the old code first, map each behaviour to its place here, then
compare results on the same data.
