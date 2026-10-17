# How the optimizer is put together

Every entry point runs the same four stages: load, solve, snapshot, act. Loading and acting do I/O and run once, in
the parent process. Solving is pure computation on data already in memory, and runs once per portfolio, in parallel
worker processes. Most of the design follows from keeping those two kinds of work apart.

## One run, end to end

A run starts in a module of `jobs/` with a validated request, such as `OptimizeRequest`, and a `Context`.
`load_market` in `inputs.py` fetches the data every portfolio shares: security analytics, the buy universe, signals
and benchmarks, loaded concurrently and merged into a single securities frame. `load_portfolios` fetches accounts and
tax lots in batches of a thousand accounts per query. The job then builds one `Task` per portfolio (a portfolio plus a
`Strategy`) and hands the tasks to `solve_all` in `engine.py`.

In a worker, `solve_task` turns the market and one portfolio into `Inputs`, applies the strategy's rules in order, and
calls `optimizer.solve`. The result is either `Solved` (trades, post-trade positions, a dollar summary, and any
constraint the lot rounding broke) or `Unsolved` (the solver status or the error). Back in the parent,
`outputs.snapshots` writes the run to a directory: the request, every input frame, every result frame and the orders.
Its `solver_inputs` frame is exactly what each LP saw after the rules.
Only after the snapshot is on disk does `optimize` submit orders, and only for results that rounding left intact.

## Requests and the context

Each entry point takes two things. Its request is a strict pydantic model of what was asked: the strategy, the
accounts, the business date. The CLI builds requests from its arguments, and an API or an agent
can build the same models from JSON. Because the request goes into the snapshot whole, every run records exactly what
produced it.

The `Context` is everything the process is connected to: the loader with its sources and cache, the OMS client, and
the snapshot directory. `open_context` in `context.py` is the only place that opens connections, and it closes them on
exit. Jobs never read settings or build clients, so a new entry point needs no infrastructure code, and a test can give a
job fake sources and a fake OMS through the same `Context`.

Configuration follows the same split. Each source module owns its settings model (`OracleSettings` beside the Oracle
connection code), and `Settings` composes them under nested environment variables such as `OPTIM_ORACLE__DSN`.

## Every run is a Job

Each run is one AKS Job running the CLI. Airflow starts it with the command's arguments, and it loads, solves,
snapshots, acts and exits. The exit code is the run's status: 0 only when every portfolio solved cleanly. Neither
Kubernetes nor Airflow retries a run, because a rerun of `optimize --submit` could send its orders twice.

`OPTIM_WORKERS` sizes the run's process pool and should match the Job's CPU request. A run with one task, the common
intraday case, solves in the Job's own process and starts no pool. Snapshots go to an Azure Files volume, so they
outlive the Job's pod.

## Why workers do no I/O

Workers hold no database connections. The parent ships the `Market` to each worker process once, through the pool
initializer, and each task carries one portfolio, which is a few kilobytes. Three things follow. Connection counts stay
bounded however many processes run. Every portfolio in a run sees the same snapshot of the market, so results are
consistent with each other and reproducible from the snapshot. And moving from one machine to a cluster changes one
function: `solve_all` would ship the market with `ray.put` or Dask's `scatter` instead of an initializer, and submit
the same tasks.

The cost is that the parent is the bottleneck for loading. That is the right place for it: loading is I/O-bound and
batched, and caching makes it cheaper still.

## Datasets: schema, query and cache rule in one place

A `Dataset` declares everything about one table: the pydantic row model that is its schema, which source and query
produce it, its unique key, how long it may be cached, and any cross-row check. The declarations live in `datasets/`,
one module per subject, next to their SQL. One generic `Loader` serves all of them, so adding data never means adding
a loader class.

Rows are validated column by column: one pydantic `TypeAdapter(list[T])` per field, built from the row model. For
100,000 bonds this takes about 85ms, against about 460ms for building a model per row and 45ms for no validation at
all. Validation stays on even in the hot path. The constraint is that field rules must live in the annotation
(`Annotated[float, Field(gt=0)]`). A `@field_validator` would silently not run, so the loader refuses models that
have one. The annotations also choose the pandas dtype. A `Literal` becomes an ordered categorical, which is why a
rule can write `rating <= "A-"`.

Caching is a property of the data, not of the code that reads it. A dataset's `CachePolicy` says how long its entry
lives, whether `optim warm` fills it, and whether a run may read the source when the entry is missing. Holdings and
accounts have no policy, so they are always read live. Every Job is a new process, so the cache is Redis, shared by all
of them. Nothing is cached in process memory: a job that needs a dataset twice loads it once and passes the frame, as
`Market` does for every portfolio in a run. Cache keys include the query parameters, the SQL text and the row schema.
The parameters include the business date, so each day starts with new entries, and a deploy that changes a query or a
schema never reads what the old code cached.

`optim warm` is the morning Job. It finds every declared dataset whose policy has a warm hook, asks the hook which
parameters to load it with, reads each set live, and overwrites its entry, so the day's runs read them from Redis. A
failed load does not stop the others, but it fails the Job, as Redis failing does. A run
treats Redis being down as a miss and lets each dataset's policy decide: read the source, or fail. A dataset that is
too heavy to read once per Job sets `live_on_miss=False`, so an outage cannot turn every Job into a full read of the
source. Without Redis, or with `--no-cache`, a run reads everything live.

Batched queries bind a list of ids as one JSON array string, which the SQL unpacks with `OPENJSON` (SQL Server) or
`JSON_TABLE` (Oracle). One bind per batch avoids Oracle's 1,000-item `IN` limit and keeps SQL out of string
formatting.

## Strategies are JSON, and the JSON schema is the code

A `Strategy` is a list of rules, an objective and a list of constraints. Each rule and constraint is a small pydantic
class whose fields are its parameters and whose method does the work: `apply` for a rule, `build` for a constraint.
Rules are grouped by family in `rules/`, constraints in `optimizer/constraints/`, and each package's `__init__.py`
collects its classes into a discriminated union on `kind`. A strategy file therefore validates strictly, an unknown
`kind` or a typo'd field is rejected, and `optim schema` prints a JSON schema that traders' tools and agents can write
against. There is no registry to keep in sync: the union is the registry.

Rules never drop rows. They narrow what may be bought (`Inputs.restrict`) or adjust what the solver believes
(`expected_return`). A bond that a rule forbids buying may still be held, and the solver must be able to sell it.

## The LP

The decision variables are par bought per security and par sold per tax lot. Selling by lot rather than by bond is what
makes the optimizer tax-aware: given two lots of one bond, it sells the one bought above today's price and keeps the
one with a gain. The objective is expected after-tax return over a horizon, less taxes and trading costs, optionally
less a penalty on distance from a target. Constraints are written in weights, fractions of pre-trade value, so they
share one scale and one feasibility tolerance. Every constraint builds from numpy and sparse scipy arrays. Group
limits, for example, are one sparse membership matrix times the weight vector, which keeps cvxpy's compile time flat
as the universe grows.

The model holds variables only for bonds that can be bought or are held. Benchmark constituents outside that set add a
constant to tracking error and are left out without changing the answer. With the example strategy, a 100,000-bond
market and a 10,000-bond solve take about 205ms per portfolio: rules 40ms, building the model 30ms, cvxpy compile 50ms,
HiGHS 60ms.

The LP trades fractional par, and the market trades lots with a minimum piece. After the solve, `optimizer.rounding`
rounds buys down and sells up, both toward cash, so position limits, group limits and the cash floor survive rounding.
Every constraint is then re-evaluated at the rounded trades, and any breach is recorded on the result. A result with a
breach is snapshotted but never ordered.

## Three entry points, one engine

The entry points differ only in what they feed the engine. `optimize` solves real accounts and submits orders. A model
portfolio is the same solve for a synthetic all-cash account that pays no tax. Transition analysis solves one account
once per tax budget, adding a `MaxTax` constraint each time, and reads the results as a frontier of tax paid against
distance from the target. Because transitions are just more tasks, their frontier points solve in parallel like any
batch.

## Logs are written for agents

Every record is one JSON object per line on stderr, because the main reader is an agent, and Azure Monitor parses the
same lines; `OPTIM_LOG_FORMAT=text` is for a person at a terminal. Logging lives in the shared parts, not in jobs or
rules: the loader, the SQL and HTTP adapters, the engine and the solver each log what passes through them, so a new
dataset, rule or entry point is logged without a line of logging code. At INFO a run tells its story: what loaded, each
portfolio's result and trades, the snapshot, the orders sent. `OPTIM_LOG_LEVEL=DEBUG` adds a record for every load
(with its cache outcome), query (connect, execute and fetch times), HTTP call, rule (what it made unbuyable) and LP
(size, settings, and build, compile and solver times).

Records stay small, because AKS splits lines over 16KB. Bulk data goes in the snapshot: the logs say what happened, and
`solver_inputs` holds exactly what each LP was given.

An exception is logged once, where its traceback is whole: by the CLI, or in the worker for a solve, so one portfolio's
failure is logged and the batch goes on. The record carries the error, the notes added on the way up (the loader names
the dataset and its parameters; an HTTP error carries the server's reply), the root cause, and `where`: the line in
this package where it surfaced, even when a library raised it.

## Known limits

These are deliberate shortcuts, marked `ponytail:` in the code.

- **Lot rounding.** Rounding after an LP breaks constraints the LP made binding. Building a model portfolio against the
  example strategy at $100mm leaves about 0.08% more cash than the cap and about 0.008 years less duration than the
  band. Verification catches this, but the result cannot trade. The upgrade is a small repair MIP over only the bonds
  the LP traded, with integer lots and min-piece binaries. HiGHS solves MIPs, so this needs no new dependency.
- **Name-level tracking.** An L1 penalty against a 50,000-name index asks a $1mm account for thousands of positions
  below the minimum piece, and rounding erases them. For small accounts, track exposures instead (duration buckets,
  sector, state, rating) with group constraints on the difference from the target.
- **Wash sales** are blocked only for bonds held at a loss in open lots. Sales in the last 30 days need a dataset.
- **Snapshots** write the whole market every run, to a local directory. Store the market once per content hash if
  storage grows too fast.
