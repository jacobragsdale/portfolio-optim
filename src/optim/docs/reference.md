# Reference

The model contract, the command line, the settings, the snapshot files and the pytest plugin of `optim` 0.1.0.

## The model module

A model is a module, or a package's `__init__.py`, listed under `[project.entry-points."optim.models"]` in its repo's
`pyproject.toml`, as `NAME = "package.module"`. A command names a model by that `NAME`, or by its module path. A
module path names a model that is not an entry point too, such as `optim.examples.cvxpy`.

| Export | Required | Signature | Runs |
|---|---|---|---|
| `Params` | yes | a subclass of `optim.Params` | Validated from the params file once per run, before anything loads. |
| `Data` | yes | a subclass of `optim.Datasets` | Loaded once per run, in the parent process. |
| `decide` | yes | `decide(prepared, shared, params) -> optim.Decision` | Once per portfolio, in a worker; one at a time, in `order`, when `reserve` is defined. |
| `prepare` | no | `prepare(data, params) -> shared` | Once per run, in the parent. Without it, `shared` is the run's `Data`. |
| `prepare_portfolio` | no | `prepare_portfolio(portfolio, shared, params) -> prepared` | Once per portfolio, always in parallel. `portfolio` is the portfolio's slice of `Data`. Without it, `prepared` is that slice. |
| `reserve` | no | `reserve(shared, decision) -> shared` | After each accepted decision, with the rounded decision. Returns the shared state the next portfolio decides with. |
| `order` | no | `order(prepared, shared, params) -> Iterable[str]` | Once, when `reserve` is defined. `prepared` maps each prepared portfolio's id to what `prepare_portfolio` returned. Must return each of those ids once. Without it, portfolios decide in the run's order. |
| `check` | no | `check(prepared, decision) -> Iterable[str]` | Once per decision, with the rounded decision. Returns the reasons to reject it; none accepts it. |
| `Metrics` | no | a subclass of `optim.Metrics` | Declares the metrics decisions report. A model that reports metrics must declare them all. |

`shared` and `prepared` must pickle: they travel to and from worker processes. An exception in `prepare_portfolio`,
`decide` or `check` rejects that portfolio alone, with `error: ...` as its reason. An exception in `prepare`, `order` or
`reserve` fails the run.

A model that is a package may also hold `__main__.py`, which runs the model when the file is run, through
[`optim.ide`](#optimide). It is the one module of a model that may reach I/O: workers import the model, never its
`__main__`, so the import check leaves it out.

## `optim.Params`

The base of a model's `Params`, and of any model nested in them. It refuses unknown keys, does not coerce types (a JSON
integer is a valid float, a string is not a number), and is frozen. Field docstrings become the field descriptions
`optim describe` prints. A params file is one JSON object; a field it leaves out takes its default.

## `optim.Datasets`

The base of a model's `Data`. Each class attribute that is an `optim.Dataset` is a dataset the model reads; on an
instance, the same attribute is that dataset's `DataFrame`. It declares `holdings = HOLDINGS` and
`securities = SECURITIES`, which a model may replace with a dataset of the same row model.

| Attribute | Type | Description |
|---|---|---|
| `frames` | `dict[str, DataFrame]` | Each dataset's frame, by the dataset's `name`. |
| `as_of_date` | `date` | The run's business date. |
| `portfolio_id` | `str \| None` | The portfolio a slice holds; `None` for the run's whole data. |

A portfolio's slice holds that portfolio's rows of each dataset whose row model has a `portfolio_id` field, and every
other dataset whole. Each frame in a slice is a shallow copy. Two datasets of one `Data` must not share a `name`.

## `optim.Dataset`

```python
Dataset(name, row, source, query, key, cache=None, check=None)
```

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Names its frame and its snapshot file. |
| `row` | `type[optim.Row]` | The row model. Field names are column names; each field's annotation sets its dtype and holds its rules. A row model with a `@field_validator` or `@model_validator` is refused. |
| `source` | `"oracle"`, `"mssql"`, `"rest"` or `"file"` | Where rows come from. |
| `query` | `str \| Path` | A SQL file for `oracle` and `mssql`, a URL path for `rest`, a Parquet or CSV file's path or URL for `file`. `{as_of_date}` in a file's path becomes the run's date. |
| `key` | `tuple[str, ...]` | Columns whose values no two rows share; a duplicate fails the load. |
| `cache` | `timedelta \| None` | How long a cache entry stays fresh; `None` reads the dataset live on every load. |
| `check` | `Callable[[DataFrame], None] \| None` | Rules across rows, which raise `ValueError` on rows that disagree. |

`per_portfolio` is true when `row` has a `portfolio_id` field. `file(path)` returns the same dataset read from a file,
uncached.

Every query binds the business date as `as_of_date` (`:as_of_date` for Oracle, `%(as_of_date)s` for SQL Server, a query
parameter for REST). A per-portfolio dataset also binds `portfolios`, a JSON array of up to 1,000 ids per query, which
SQL Server unpacks with `OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$')` and Oracle with `JSON_TABLE`. Oracle
refuses a bind its SQL does not use. A file source filters a per-portfolio file to the batch's `portfolio_id`s. Field
types for rows are in `optim.schema`: `Id`, `Cusip`, `Isin`, `Currency`, `Price`, `Quantity`, `Weight` and `Rating`, an
ordered category best first.

## Shared datasets

`optim.datasets` holds the datasets every model reads. A dataset only one model reads belongs in that model's repo.

`HOLDINGS`: `Holding` rows from SQL Server, read per portfolio, key `portfolio_id, security_id`, never cached. Working
holdings: the start of the day plus what has traded since.

| Column | Type | Description |
|---|---|---|
| `portfolio_id` | id | |
| `security_id` | id | |
| `quantity` | float | Face amount for a bond, currency units for cash. Cash may be negative. |

`SECURITIES`: `Security` rows from Oracle, read whole, key `security_id`, cached 15 minutes.

| Column | Type | Description |
|---|---|---|
| `security_id` | id | The canonical id every dataset joins on. |
| `cusip`, `isin` | str or null | Identifiers the security may also have. |
| `asset_class` | `bond`, `cash` or `other` | A holding of a `cash` security is the portfolio's cash. |
| `currency` | ISO 4217 code | |
| `issuer`, `sector` | str or null | |
| `rating` | rating or null | An ordered category, best first. |
| `maturity` | date or null | |
| `price` | float > 0 | Clean price per `price_per` of quantity. |
| `accrued` | float | Accrued interest per `price_per` of quantity. |
| `price_per` | int > 0 | 100 for a bond quoted in percent of par, 1 for cash. |
| `increment` | float > 0 | Every trade is a whole number of increments. |
| `min_piece` | float > 0 or null | The smallest trade, where the market sets one. |
| `min_remaining` | float >= 0 or null | The smallest holding a partial sale may leave. |

`unit_value(securities)` returns what one unit of each security's quantity is worth: `(price + accrued) / price_per`.

## `optim.Decision`

| Field | Type | Default | Description |
|---|---|---|---|
| `buy` | `dict[str, float]` | `{}` | Quantity to buy, by security id. A `pd.Series` indexed by security id is accepted. |
| `sell` | `dict[str, float]` | `{}` | Quantity to sell, by security id. |
| `solved` | `bool` | `True` | Whether the problem was solved. A decision that was not is rejected. |
| `objective` | `float \| None` | `None` | The objective's value. |
| `solver` | `SolverStats \| None` | `None` | How the solver stopped. |
| `metrics` | `dict[str, float]` | `{}` | Numbers for sweeps. A `Metrics` instance is accepted. |

A quantity that is not a finite number is refused when the decision is made. `SolverStats` has `solver`, `status` (the
solver's own words), `stop` (`optimal`, `limit`, `imprecise`, `infeasible`, `unbounded` or `failed`), `gap`, `nodes`,
`iterations` and `seconds`.

## `optim.cvxpy`

`SolveSettings`, a strict params model to nest in a model's `Params`:

| Field | Type | Default | Description |
|---|---|---|---|
| `solver` | `"HIGHS"` or `"COPT"` | `"HIGHS"` | COPT needs the `copt` extra; without a license it runs size-limited. |
| `time_limit_s` | float > 0 | `30.0` | A solve that reaches it is not solved. |
| `mip_gap` | float >= 0 | `0.001` | The relative gap at which an integer answer counts as optimal. |
| `options` | `dict` | `{}` | Passed to the solver by its own option names; they override the fields above and COPT's one thread. |

`solve(problem, settings, *, securities, buy=None, sell=None, metrics=None) -> Decision` solves `problem` with
`warm_start=False`, then reads it. `read(problem, *, securities, buy=None, sell=None, metrics=None) -> Decision` reads a
problem something else solved. `buy` and `sell` are cvxpy expressions in the holdings' units, one entry per id in
`securities`. `metrics` maps names to expressions or numbers, evaluated at the solution. A trade smaller than 1e-6 is
dropped. The decision is solved only when the solver stopped `optimal`; without a solution it has no trades. A problem
with no variables is solved with no trades, without calling a solver.

## Rounding and acceptance

After `decide`, the core rounds each quantity to the nearest whole number of its security's `increment`, sells the whole
holding where a sale is within half an increment of it, and drops a quantity that rounds to zero. It then rejects the
rounded decision, with these reasons, when:

| Reason | When |
|---|---|
| `it has no holdings: is its id right?` | The portfolio has no rows in `holdings`. |
| `not solved: STOP` | `solved` is false. |
| `trades securities that are not loaded: [...]` | A trade's security is not in `securities`. |
| `buys quantities that are not positive: [...]`, `sells ...` | A quantity is zero or negative. |
| `sells Q of S, more than its holding of H` | A sale exceeds the holding by more than the tolerance. |
| `leaves cash of C: it spends more than it has` | Cash holdings plus sales less buys, at unit values, is below minus the tolerance. |

The tolerance is 1e-6 of the portfolio's value, its holdings at unit values. The model's `check` adds its reasons
after the core's.

## `optim.testing`

`frame(dataset, *rows, default=None) -> DataFrame` builds `dataset`'s validated frame from partial rows; a row's missing
fields come from `default`, or for holdings and securities from `HOLDING` and `BOND`, and are otherwise null.
`data(Data, *, as_of_date=AS_OF, portfolio_id=None, **frames) -> Data` builds a model's data from frames by attribute;
a dataset not given has no rows. `BOND` is bond `A` at par in increments of 1,000; `CASH` is security `USD`; `HOLDING` is
P1's $1mm of cash; `AS_OF` is 2026-10-16.

## `optim.research`

| Function | Returns |
|---|---|
| `open_snapshot(root)` (from `optim.run`) | A `Snapshot`: its `info` (`run.json`), `model`, `params` and `data`, read with the code you have now. |
| `trial(snapshot, portfolio_id, params=None)` | A `Trial`: `prepared`, `shared`, `problem` (the cvxpy problem `optim.cvxpy` solved, or `None`) and `outcome`. |
| `sweep(snapshot, variants, *, workers=1)` | The sweep table. |
| `regress(model, snapshots, *, last=20, labels=None, workers=1)` | The regression table. |
| `describe(model)` | The `optim describe` JSON. |

`storage.Root.at(location)` opens a path or URL as a `Root`.

## `optim.ide`

Running a model by running its `__main__.py`, as from an IDE, which calls one of these with its own `__file__`:

```python
import optim.ide

if __name__ == "__main__":  # each worker process imports this file too, and must not start a run of its own
    optim.ide.run(__file__)
```

| Function | Runs as |
|---|---|
| `run(file, *, params, portfolios, as_of, data, labels, workers, cache)` | `optim run` |
| `replay(file, run_id, *, params, portfolios, labels, workers)` | `optim replay` |

Each finds the model whose package holds `file`, by walking up the directories that have an `__init__.py`, and its
repo, the nearest directory above with a `pyproject.toml`. It names the model as the repo's entry points list it, or by
its module path. It moves to the repo's root, so a relative path is the repo's, reads the repo's `.env` into the
environment, where a variable already set wins, and logs as text unless `OPTIM_LOG_FORMAT` says otherwise. It prints
what the command prints, and returns the `Run`, with its `run_id`, `outcomes` and `ok`. An error raises, rather than
being logged. A repo with no `.env` fails unless `OPTIM_SNAPSHOT_ROOT` is set.

| Argument | Default | Description |
|---|---|---|
| `params` | the model's default params | A params file: `params/MODEL/default.json`, or `params/default.json` beside the model, as optim's examples keep it. |
| `portfolios` | every portfolio of the fixture | Portfolio ids. With neither `portfolios` nor `data`, `run` decides the model's fixture, `tests/MODEL/fixture/` or `fixture/` beside the model, as of the fixture's date. |
| `as_of` | the fixture's date, or today in New York | A `date`, or `YYYY-MM-DD`. |
| `data` | the fixture, or none with `portfolios` | As `--data`: each dataset that has a file in the directory is read from it, the others from their sources. |
| `run_id` | required | For `replay`: a run under `OPTIM_SNAPSHOT_ROOT`, or a snapshot's path or URL. |
| `labels`, `workers`, `cache` | none, `OPTIM_WORKERS`, on | As `--label`, `--workers` and `--no-cache`. |

## Commands

Every command logs JSON lines on stderr and exits 1 on an error, logged once with its root cause.

### `optim run`

```text
optim run MODEL --params FILE [--as-of DATE] [--portfolios ID ...] [--data DIR] [--label KEY=VALUE] [--workers N] [--no-cache]
```

Validates the params, loads the model's data, decides each portfolio, saves the snapshot under `OPTIM_SNAPSHOT_ROOT`,
and writes accepted orders where `OPTIM_ORDERS__*` is set. Prints `run RUN_ID`, a row per portfolio, and each reason.
Exits 0 only when every portfolio was accepted.

| Option | Default | Description |
|---|---|---|
| `--params` | required | The params JSON file. |
| `--as-of` | today in New York | The business date, `YYYY-MM-DD`. |
| `--portfolios` | with `--data`, every portfolio in its holdings file | Portfolio ids, or `@FILE` with one per line. |
| `--data` | none | Reads each dataset that has a file in the directory, `NAME.parquet` or `NAME.csv`, instead of its source. |
| `--label` | none | `KEY=VALUE`, recorded in `run.json`; repeat for more. |
| `--workers` | `OPTIM_WORKERS` | Worker processes. |
| `--no-cache` | off | Reads every dataset live. |

### `optim replay`

```text
optim replay RUN [--params FILE] [--portfolios ID ...] [--label KEY=VALUE] [--workers N]
```

Decides a saved run again with the code installed now, and other params if given, and saves it as a new run under
`OPTIM_SNAPSHOT_ROOT`. `RUN` is a run id under `OPTIM_SNAPSHOT_ROOT`, or a snapshot's path or URL. Reads no source and
writes no orders. A replay of a subset decides that subset; for a model that reserves, the portfolios before them in
its order decide again too. A replay of a replay of a subset replays that subset. Exit status as `run`.

### `optim sweep`

```text
optim sweep RUN VARIANTS [--out FILE] [--workers N]
```

Decides the run once per `*.json` params file in the directory `VARIANTS`, every file validated first, and writes one
table, as CSV on stdout or to `--out` as `.csv` or `.parquet`. Reads only the snapshot and saves none. Columns:
`variant` (the file's stem), `portfolio_id`, `accepted`, `objective`, `turnover`, `trades`, `cash`, `solve_seconds`,
`seconds`, one column per metric, and `reasons`, joined with `; `. Exits 0.

### `optim regress`

```text
optim regress MODEL [--snapshots ROOT] [--last N] [--label KEY=VALUE] [--out FILE] [--workers N]
```

Replays the model's `--last` (default 20) most recent runs under `--snapshots` (default `OPTIM_SNAPSHOT_ROOT`), replays
aside and only those with every `--label` given, with each run's own params, and writes a row per change: `run_id`,
`portfolio_id`, `status_before`, `status_after`, `limit` (either decision stopped at a time limit), and for a changed
trade `security_id`, `side`, `before` and `after`, the rounded quantities. Exits 1 if anything changed.

### `optim describe`

```text
optim describe MODEL
```

Prints JSON: `model`, `module`, `optim_version`, `hooks` (the optional hooks it defines), `params` (the JSON schema),
`datasets` (by attribute: `name`, `source`, `per_portfolio`, `key`, `cache_seconds`, `columns`) and `metrics` (the JSON
schema, or null).

### `optim new repo` and `optim new model`

```text
optim new repo NAME (--index URL | --optim-path PATH) [--from cvxpy|plain] [--model MODEL] [--dir DIR] [--no-sync]
optim new model NAME [--from cvxpy|plain] [--no-sync]
```

`new repo` makes `DIR/NAME` (default here) with package `NAME`, one model `MODEL` (default `NAME`) copied from the
example (default `cvxpy`), the templates and the agent files, then runs `git init` and `uv sync`. The repo depends on
`optim>=0.1,<0.2`, from the source its `pyproject.toml` names: `--index`, or `UV_INDEX`, the internal index, or
`--optim-path`, a checkout of `optim`, editable, to try a change to it. It refuses to make a repo with neither, or both:
PyPI has an unrelated package named `optim`, which a repo with no source would install. `new model` adds a model and its
entry point to the repo in the working directory, and a row to the table of models in its `README.md`, then runs
`uv sync`. Each model gets a `__main__.py` that runs it, through `optim.ide`. Hyphens in a name become underscores in
its module. `--no-sync` leaves `uv sync` out.

### `optim sync`, `optim docs` and `optim warm`

`optim sync` writes the contract block into the working directory's `AGENTS.md`, `@AGENTS.md` into `CLAUDE.md`, and
copies of the `optim-*` skills into `.agents/skills/` and `.claude/skills/`, deleting copies of skills `optim` no longer
ships; it prints each path it wrote. `optim docs [PAGE]` prints a page of these docs, or lists them. `optim warm
MODEL ... [--as-of DATE]` reads every cached dataset the models read whole, live, and overwrites its cache entry; it
needs `OPTIM_CACHE_ROOT` and exits 1 if any load failed.

## Settings

Settings come only from environment variables. A source that is not set cannot be read.

| Variable | Default | Description |
|---|---|---|
| `OPTIM_SNAPSHOT_ROOT` | required | Where runs save snapshots and replays read them: a path, or a URL `pyarrow.fs` opens, such as `abfs://...`. |
| `OPTIM_CACHE_ROOT` | none | The dataset cache, a path or URL; without it nothing is cached. |
| `OPTIM_WORKERS` | `1` | Worker processes per run; `1` decides in the calling process. |
| `OPTIM_ORACLE__DSN`, `__USER`, `__PASSWORD`, `__MODE`, `__CLIENT_LIB_DIR` | none | Oracle: `MODE` is `thin` (no client libraries) or `thick`, which loads Oracle Instant Client from `CLIENT_LIB_DIR` or the library path. |
| `OPTIM_MSSQL__CONNECTION_STRING` | none | SQL Server, the primary: holdings must include the trades just booked. The database must allow snapshot isolation. |
| `OPTIM_REST__URL` | none | A JSON API that answers a GET with a list of records. |
| `OPTIM_<SOURCE>__MAX_CONCURRENT`, `__TIMEOUT_S`, `__MAX_ROWS` | required with the source | Calls open at once, seconds per call, and the most rows a call may return. |
| `OPTIM_ORDERS__CONNECTION_STRING`, `__TABLE`, `__TIMEOUT_S` | none | Production only: where accepted orders are inserted, as `run_id, portfolio_id, security_id, side, quantity`. `TABLE` is `schema.table`. |
| `OPTIM_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, for `optim`'s and models' loggers. |
| `OPTIM_LOG_FORMAT` | `json` | `json` lines, or `text` for a terminal. |
| `OPTIM_GIT_SHA`, `OPTIM_IMAGE` | none | Set in an image: the commit and image a run records. |

## Snapshot files

`OPTIM_SNAPSHOT_ROOT/RUN_ID/` appears only once it is complete, and nothing changes it afterwards.

```text
RUN_ID/
  run.json
  inputs/<dataset>.parquet   each dataset as loaded, validated on read by the model's row models
  decisions.parquet          a row per portfolio
  trades.parquet             a row per trade
  reasons.parquet            a row per reason a decision was rejected
  metrics.parquet            a row per portfolio and metric
```

`run.json`:

| Key | Value |
|---|---|
| `run_id` | A UUIDv7. |
| `optim_version` | The `optim` the run ran. |
| `model` | `name`, as the run named it, and `module`. |
| `as_of_date` | The business date. |
| `portfolios` | Every portfolio the run loaded, in the order asked; a replay keeps its run's. |
| `params` | Every param, defaults included. |
| `params_file` | `path`, `sha256` and `git_commit` of the file they came from. |
| `labels` | The `--label` pairs. |
| `code` | `git_sha`, `image` and `dirty` of the model's code. |
| `workers` | Worker processes. |
| `started_at`, `loaded_at`, `finished_at` | UTC times: before loading, once loaded, after the last decision. |
| `datasets` | Per dataset, its `source`, `read` (`live`, `hit`, `miss` or `refresh`) and `fetched_at`; null for a replay. |
| `order` | For a model that reserves, the order its portfolios decided in; otherwise null. |
| `replay` | For a replay, `of`, the run it replayed, and `portfolios`, the subset it decided; otherwise null. |

`decisions.parquet` columns: `portfolio_id`, `status` (`accepted` or `rejected`), `trades`, `value` (before trading),
`cash` (after trading), `turnover`, `solved`, `objective`, `solver`, `solver_status`, `stop`, `gap`, `nodes`,
`iterations`, `solve_seconds` and `seconds`. `trades.parquet`: `portfolio_id`, `security_id`, `side`, `quantity`
(rounded; 0 where rounding left nothing) and `decided` (as the model decided it). `reasons.parquet`: `portfolio_id`,
`reason`. `metrics.parquet`: `portfolio_id`, `metric`, `value`.

## The pytest plugin

`optim` registers a pytest plugin. In a repo whose `pyproject.toml` lists models, a run of pytest without paths adds,
for each model `NAME`:

| Test | Fails when |
|---|---|
| `pyproject.toml::params[NAME]` | `params/NAME/default.json` is missing or does not validate. |
| `pyproject.toml::fixture[NAME]` | Deciding every portfolio of `tests/NAME/fixture/` with the default params rejects one. The fixture holds a `NAME.csv` or `NAME.parquet` for every dataset, and `fixture.json`, `{"as_of_date": "YYYY-MM-DD"}`. Also fails when decisions report a metric `Metrics` does not declare, or do not report one it declares. |
| `pyproject.toml::imports[NAME]` | An import chain leads from the model's module, its `__main__` aside, to a source, the loader, the cache, outputs, a database driver or an HTTP client. |
| `pyproject.toml::agent-files` | `AGENTS.md`'s block, `CLAUDE.md` or the `optim-*` skills do not match what `optim sync` writes. |

The plugin registers the `e2e` marker and these ini options:

| Option | Default | Description |
|---|---|---|
| `optim_qa_markers` | `["qa"]` | Text, matched without case, that every configured source and destination must contain. |
| `optim_e2e_writes` | `[]` | The only snapshot roots, cache roots and orders tables the e2e suite may write to, as the environment sets them. |
| `optim_e2e_command` | this interpreter's `-m optim` | The command that runs the deployed `optim`. |

The session fixture `optim_qa` fails, naming each problem, unless every configured source and destination carries a
marker and every write is on the allowlist. It returns a `QA` with `portfolios(sql_file, *, source="mssql", **binds)`,
which runs a `SELECT` or `WITH` query in a read-only transaction and returns its `portfolio_id`s; `run(model, params,
portfolios, *args)` and `replay(run)`, which run the command from the repo's root; and `smoke(model, params, portfolios, *args)`, which
fails unless the run completes, every portfolio is accepted or rejected with reasons, and its replay gives the same
trades for the decisions that did not stop at a time limit.
