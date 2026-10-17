# portfolio-optim

A tax-aware optimizer for taxable fixed income portfolios: munis and corporates, held in tax lots. It loads holdings,
the buy universe, signals, benchmarks and security analytics from Oracle, SQL Server and REST. It applies pre-solve
rules, then solves one LP per portfolio with cvxpy and HiGHS, in parallel processes. Every run is saved as a snapshot,
and orders go to the OMS.

## Quick start

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.14 on first use.

```sh
uv sync
uv run pytest
```

The tests run every entry point against in-memory fake sources, so they need no database or network.

To run against real systems, copy `.env.example` to `.env` and fill in every variable, then run a strategy for one
account:

```sh
uv run --env-file .env optim optimize --strategy strategies/core-muni.json --accounts ACCOUNT_ID
```

`ACCOUNT_ID` is an account in the SQL Server `accounts` table. The run prints one summary row per portfolio and writes
a snapshot under `OPTIM_SNAPSHOT_DIR`. It submits no orders until you add `--submit`. `uv run optim --help` lists the
other commands: `model`, `transition` and `schema`.

## Layout

| Path | Holds |
|---|---|
| `src/optim/inputs.py` | Every dataset: its row schema, query and cache rule. How they combine into `Market` and `Inputs`. |
| `src/optim/queries/` | One SQL file per database dataset. |
| `src/optim/rules.py` | Pre-solve rules. |
| `src/optim/lp.py` | The LP: variables, objective, constraints, lot rounding, results. |
| `src/optim/run.py` | `Strategy`, the JSON contract, and solving tasks in-process or in a process pool. |
| `src/optim/jobs.py` | The entry points: optimize, model portfolio, transition analysis. |
| `src/optim/outputs.py` | Orders and snapshots. |
| `src/optim/data.py` | The loader: pydantic row validation into typed DataFrames, batching, caching. |
| `src/optim/sources.py`, `cache.py`, `settings.py` | Oracle, SQL Server and REST adapters; memory and Redis caches; environment config. |
| `strategies/` | Example strategy files. |

## Further reading

- [How the optimizer is put together](docs/architecture.md): how a run flows, and why it is built that way.
- [Extending the optimizer](docs/extending.md): add a dataset, rule, constraint, source or entry point.

## Development

`uv run pre-commit run --all-files` runs ruff, ty and the lockfile check. The git hook runs the same checks on every
commit after `uv run pre-commit install`.
