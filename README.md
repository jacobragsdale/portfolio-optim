# portfolio-optim

An optimizer for taxable fixed income SMA portfolios. "Taxable" describes the mandates: the optimizer trades holdings
by security, and neither selects tax lots nor harvests losses. It loads holdings, sleeves, eligibility lists, trading
controls, cash flows, signals, benchmarks and security analytics from Oracle, SQL Server and REST. It applies each
portfolio's controls and the strategy's rules, then solves each portfolio, all its sleeves together, with cvxpy and
HiGHS or COPT, in parallel processes: as an integer program that trades exact denominations, or as a faster LP or QP
whose trades are rounded. Every result is rebuilt from its exact trades and checked before it may trade, and every run
is saved as a snapshot, and can be replayed from it.

The engine is ready; its datasets and business logic are placeholders. The queries, prep, rules, constraints and
objective terms here are one small example of each, to be replaced by an existing optimizer's
([porting another optimizer](docs/porting.md)). `grep -rn "TODO(port)" src tests` lists every place the port writes
code.

## Quick start

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.14 on first use.

```sh
uv sync
uv run pytest
```

The tests run every stage, from the loader to the snapshot and its replay, on rows built in memory, with no database.
`uv sync` also installs COPT, which the tests run without a license, size-limited.

To run against real systems, copy `.env.example` to `.env` and fill in every variable, then run a strategy for one
portfolio:

```sh
uv run --env-file .env optim optimize --strategy strategies/core.json --portfolios PORTFOLIO_ID
```

`PORTFOLIO_ID` is an `account_id` in the SQL Server `accounts` table that `src/optim/datasets/sql/portfolios.sql`
queries; until the port, those queries name made-up tables. The run prints one summary row per portfolio, whose
`status` is `accepted` when its trades may trade, and writes a snapshot under `OPTIM_SNAPSHOT_ROOT`. `--holdings sod`
starts from the start of the day's holdings rather than working ones. `uv run optim --help` lists the other commands:
`replay`, `warm`, `clear` and `schema`.

To try a strategy without access to those systems, replay a saved run with it. Replay reads only the run's snapshot,
and without `--strategy` re-runs the run as it was, with the code you have now:

```sh
uv run optim replay snapshots/RUN_ID --strategy strategies/track-benchmark.json
```

## Run as a Job

```sh
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t <registry>.azurecr.io/portfolio-optim:$(git rev-parse HEAD) .
```

The build fails without `GIT_SHA`: every run's snapshot records the commit it ran, and the image, which the run's
environment sets as `OPTIM_IMAGE`. The image holds `strategies/` at `/strategies`, so a strategy change ships as a new
image. It includes COPT, which reads its license from `COPT_LICENSE_DIR`, and connects to Oracle in thin mode; thick mode
also needs Oracle Instant Client in the image and `OPTIM_ORACLE__MODE=thick`.

The image's entrypoint is the `optim` CLI, which Airflow runs with the command's arguments. Point `OPTIM_SNAPSHOT_ROOT`
and `OPTIM_CACHE_ROOT` at shared storage, and schedule `optim warm` each business morning, before the first run, so
runs read cached datasets (see [How to cache a dataset](docs/extending.md#how-to-cache-a-dataset)). Give each run labels, such as
`--label purpose=production --label dag_run=<Airflow's run id>`: its `run.json` records them, so production runs can be
told from research, and each run found from the DAG run that started it.

## Layout

The code is under `src/optim/`, one folder per concern and one module per thing that grows: a source, a subject of
data, a family of rules or constraints, a job. The [agent guide](AGENTS.md) maps every module in the order a run flows
through them.

## Further reading

- [The optim framework](docs/design.md): the agreed design this repo is moving to, a package that quants' model repos
  depend on, and the steps to get there.
- [How the optimizer is put together](docs/architecture.md): how a run flows, the portfolio model (positions, cash,
  sleeves, value and controls), when a result may trade, and why it is built that way.
- [Extending the optimizer](docs/extending.md): add a dataset, rule, constraint, objective term, source, solver, job or
  output; configure a solve; solve a portfolio with several sleeves; compare strategies on a saved run.
- [Porting another optimizer](docs/porting.md): bring an existing optimizer's data and logic into this one, best
  effort, with a porting log and a sanity check on the same data.
- [Snapshot files](docs/snapshots.md): what each run saves, file by file.
- [Agent guide](AGENTS.md): the code map, the rules to keep, and the checks to run, for coding agents.

## Development

`uv run pre-commit run --all-files` runs ruff, ty and the lockfile check. The git hook runs the same checks on every
commit after `uv run pre-commit install`.
