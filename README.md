# portfolio-optim

A tax-aware optimizer for taxable fixed income portfolios held in tax lots. It loads holdings,
the buy universe, signals, benchmarks and security analytics from Oracle, SQL Server and REST. It applies pre-solve
rules, then solves each portfolio with cvxpy and HiGHS, in parallel processes: as an integer program that trades exact
lot sizes, or as a faster LP whose trades are rounded. Every run is saved as a snapshot.

## Quick start

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.14 on first use.

```sh
uv sync
```

To run against real systems, copy `.env.example` to `.env` and fill in every variable, then run a strategy for one
portfolio:

```sh
uv run --env-file .env optim optimize --strategy strategies/core.json --portfolios PORTFOLIO_ID
```

`PORTFOLIO_ID` is an `account_id` in the SQL Server `accounts` table that `src/optim/datasets/sql/portfolios.sql`
queries. The
run prints one summary row per portfolio and writes a snapshot under `OPTIM_SNAPSHOT_DIR`. `uv run optim --help` lists the other commands: `model`, `transition`, `replay`, `warm`, `clear` and
`schema`.

To try a strategy without access to those systems, replay a saved run with it. Replay reads only the run's snapshot:

```sh
uv run optim replay snapshots/RUN_ID --strategy strategies/track-benchmark.json
```

## Run as a Job

```sh
docker build -t portfolio-optim .
```

The image's entrypoint is the `optim` CLI. Every run is an AKS Job that Airflow starts with the command's arguments:
`deploy/job.yaml` is the Job spec, and `deploy/snapshots-pvc.yaml` the volume that snapshots go to. Schedule
`optim warm` each business morning, before the first run, so runs read cached datasets from Redis (see
[How to cache a dataset](docs/extending.md#how-to-cache-a-dataset)).

## Layout

Each folder under `src/optim/` is one concern, with one module per thing that grows: a source, a subject of data, a
family of rules or constraints, an entry point.

| Path | Holds |
|---|---|
| `jobs/` | The entry points, one module each: `optimize`, `model_portfolio`, `transition`, `replay`. Each defines its request model. |
| `datasets/` | Every dataset by subject (portfolios, securities, signals, benchmarks): row schema, query and cache rule. SQL is in `datasets/sql/`. |
| `inputs.py` | How datasets combine into the `Market` all portfolios share and the `Inputs` one solve sees. |
| `rules/` | Pre-solve rules, by family: `eligibility`, `tax`. |
| `optimizer/` | The solve: `model`, `objective` terms, `constraints/` by family, `solve`, `rounding` to exact trades, `results`. |
| `strategy.py`, `engine.py` | The strategy JSON contract; solving tasks in-process or in a process pool. |
| `outputs/` | What a run writes after its solves: run `snapshots`. |
| `sources/` | `oracle`, `mssql` and `rest` adapters, each with its settings model, over the shared `sql` adapter. |
| `cache.py` | The Redis dataset cache that every Job shares. |
| `data/` | The loader: row validation into typed frames (`frames`), and fetching, batching and caching (`loader`). |
| `context.py`, `settings.py`, `cli.py` | Wiring: the connections a process opens, its environment config, and the command line. |
| `logs.py` | Structured logging: JSON lines, timings, and exceptions with their root cause. |
| `schema.py` | The pydantic base models and shared field types. |

## Further reading

- [How the optimizer is put together](docs/architecture.md): how a run flows, and why it is built that way.
- [Performance](docs/performance.md): measured run time, CPU and memory, from 1 to 1,000 portfolios, for sizing Jobs.
- [Extending the optimizer](docs/extending.md): add a dataset, rule, constraint, objective term, source or entry point;
  configure a solve; compare strategies on a saved run.

## Development

`uv run pre-commit run --all-files` runs ruff, ty and the lockfile check. The git hook runs the same checks on every
commit after `uv run pre-commit install`.
