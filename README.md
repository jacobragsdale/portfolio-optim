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

`ACCOUNT_ID` is an account in the SQL Server `accounts` table that `src/optim/datasets/sql/accounts.sql` queries. The
run prints one summary row per portfolio and writes a snapshot under `OPTIM_SNAPSHOT_DIR`. It submits no orders until
you add `--submit`. `uv run optim --help` lists the other commands: `model`, `transition` and `schema`.

## Layout

Each folder under `src/optim/` is one concern, with one module per thing that grows: a source, a subject of data, a
family of rules or constraints, an entry point.

| Path | Holds |
|---|---|
| `jobs/` | The entry points, one module each: `optimize`, `model_portfolio`, `transition`. Each defines its request model. |
| `datasets/` | Every dataset by subject (accounts, securities, signals, benchmarks): row schema, query and cache rule. SQL is in `datasets/sql/`. |
| `inputs.py` | How datasets combine into the `Market` all portfolios share and the `Inputs` one solve sees. |
| `rules/` | Pre-solve rules, by family: `eligibility`, `tax`. |
| `optimizer/` | The LP: `model`, `objective`, `constraints/` by family, `solve`, lot `rounding`, `results`. |
| `strategy.py`, `engine.py` | The strategy JSON contract; solving tasks in-process or in a process pool. |
| `outputs/` | `orders`, submission to the `oms`, and run `snapshots`. |
| `sources/` | `oracle`, `mssql` and `rest` adapters, each with its settings model, over the shared `sql` adapter. |
| `cache/` | The `memory` and `redis` caches. |
| `data/` | The loader: row validation into typed frames (`frames`), and fetching, batching and caching (`loader`). |
| `context.py`, `settings.py`, `cli.py` | Wiring: the connections a process opens, its environment config, and the command line. |
| `schema.py`, `utils/` | The strict pydantic base and shared field types; small helpers. |

## Further reading

- [How the optimizer is put together](docs/architecture.md): how a run flows, and why it is built that way.
- [Extending the optimizer](docs/extending.md): add a dataset, rule, constraint, source or entry point.

## Development

`uv run pre-commit run --all-files` runs ruff, ty and the lockfile check. The git hook runs the same checks on every
commit after `uv run pre-commit install`.
