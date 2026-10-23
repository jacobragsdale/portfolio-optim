# How the optimizer is put together

Every job runs the same stages: load, prepare, solve, review, save, outputs. Loading, saving and outputs do I/O and
run once, in the parent process. Preparing, solving and reviewing are pure computation on data already in memory: the
market is prepared once, in the parent, and each portfolio is prepared, solved and reviewed in parallel worker
processes. Most of the design follows from keeping those two kinds of work apart.

The optimizer is for taxable fixed income SMAs. "Taxable" describes the mandates, not the optimizer: it trades
holdings by security, and neither chooses tax lots nor harvests losses. The datasets, prep, rules, constraints, objective
terms and model formulation in this repository are placeholders until an existing optimizer is ported in
([porting another optimizer](porting.md)). This page explains the engine around them.

## One run, end to end

A run starts with a validated request, such as `OptimizeRequest`, and a `Context`, which `run` in `jobs/` takes. Its
first step creates a run id, a UUIDv7, and binds it to the logs, so every record of the run carries `run_id`, solver
workers' records included. The id also names the run's snapshot, and ids sort by when their runs started. A scheduler's
own ids, such as Airflow's DAG run, arrive as labels.

`load_frames` in `datasets/load.py` loads every dataset a run reads, one validated frame each: the data every portfolio
shares, for the business date, and each portfolio's details, sleeves, holdings, controls and cash flows, in batches of a
thousand portfolios, each batch in one read-only transaction. Everything loads at once, every batch included: see
[Loading at the pace of each source](#loading-at-the-pace-of-each-source). `build_market` in `prep/` then prepares the
`Market` that every portfolio shares, and the request's `tasks` prepares its portfolios with `build_portfolios` (see
[Preparing the inputs](#preparing-the-inputs)), making one `SolveTask`, a portfolio plus a `Strategy`, per solve. `run`
hands the tasks to `solve_all` in `engine.py`.

In a worker, `solve_task` turns the market and one portfolio into `SolverInputs`, applies the strategy's rules in order
(`apply_rules`), and calls `optimizer.solve`. The result is either `Solved`, with trades, post-trade positions, a dollar
summary and the result of its [acceptance review](#acceptance-when-a-result-may-trade), or `Unsolved` (why the solver
stopped, or the error). Back in the parent, `outputs.snapshots` writes the run's [snapshot](snapshots.md): its request
and metadata, every dataset as it was loaded, and every result frame.

Nothing derived from the datasets is stored: prep and rules are pure, so replay rebuilds what each solve saw with the
same code, and a change to prep shows on replay as a change to a rule does. Storing what each solve saw instead capped a
Job at a few thousand portfolios, since the parent holds every result until the snapshot is written. Anything else the
run produces, such as a report, rows in a database or an API call, comes after the snapshot is on disk, so a failed
output never loses the run. The engine never submits orders.

## Requests and the context

A run takes two things. Its request is a pydantic model of what was asked: the job, the strategy, the portfolios, the
business date, and which holdings they start from. The CLI builds requests from its arguments, and an API or an agent
can build the same models from JSON. Because the request goes into the snapshot whole, every run records exactly what
produced it, and replay can run it again.

The `Context` is everything the process is connected to: the loader with its sources and cache, and the snapshot
directory. `open_context` in `context.py` is the only place that opens connections,
and it closes them on exit. Jobs never read settings or build clients, so a new job needs no infrastructure code, and a
test can give a job fake sources through the same `Context`.

Configuration follows the same split. Each source module owns its settings model (`OracleSettings` beside the Oracle
connection code), and `Settings` composes them under nested environment variables such as `OPTIM_ORACLE__DSN`.

## Every run is a Job

Each run is one AKS Job running the CLI. Airflow starts it with the command's arguments, and it loads, solves, saves,
writes its outputs and exits. The exit code is the run's status: 0 only when every portfolio solved and its result was
accepted. Neither Kubernetes nor Airflow retries a run: a rerun is a new run, with its own id and snapshot.

`OPTIM_WORKERS` sizes the run's process pool and should match the Job's CPU request in physical cores: hyperthreads
add little throughput and slow every solve. Each worker's BLAS gets one thread rather than one per CPU, and COPT one
thread per solve, so the workers do not compete for cores. A run with one task, the common intraday case, solves in the
Job's own process and starts no pool. Snapshots go to an Azure Files volume, so they outlive the Job's pod.

A solver process that dies, as when the Job reaches its memory limit, breaks the pool and loses every portfolio it had
not finished. Those get one new pool, which covers a one-off kill; if that breaks too, they stay unsolved, and one
error says so. A Job that Kubernetes stops logs `stopped by SIGTERM` with its run id before it exits.

## Why workers do no I/O

Workers hold no database connections. The parent ships the `Market` to each worker process once, through the pool
initializer, and each task carries one portfolio, which is a few kilobytes. Three things follow. Connection counts stay
bounded however many processes run. Every portfolio in a run sees the same snapshot of the market, so results are
consistent with each other and reproducible from the snapshot. And moving from one machine to a cluster changes one
function: `solve_all` would ship the market with `ray.put` or Dask's `scatter` instead of an initializer, and submit
the same tasks.

The rule is enforced, not only followed: `lint-imports`, in pre-commit, fails if `prep`, `rules`, `optimizer`,
`strategy` or `engine` imports the loader, the cache, a source, the context, jobs or outputs, directly or through
another module. That is why a dataset's declaration (`data/dataset.py`) is apart from the loader that fetches it: prep
and the optimizer import row models, and must not pull the loader in with them.

The cost is that the parent is the bottleneck for loading. That is the right place for it: loading is I/O-bound and
batched, and caching makes it cheaper still.

## Datasets: schema, query and cache rule in one place

A `Dataset` declares everything about one table: the pydantic row model that is its schema, which source and query
produce it, its unique key, how long it may be cached, and any cross-row check. The declarations live in `datasets/`,
one module per subject, next to their SQL. One generic `Loader` serves all of them, so adding data never means adding
a loader class.

Loading is raw acquisition, kept apart from the numerical preparation that follows it. A dataset's frame holds what its
source returned, validated against its row model and nothing more. Every number is a float, booked values such as
prices, quantities and cash flows included: with `Decimal` columns, a quant's `holdings.quantity * 0.5` raises
`TypeError`, and arithmetic on object columns is slow. SQL Server's driver returns DECIMAL columns as `Decimal`s, and
validation turns them into floats. Rounding to each security's trading increment is what makes trade quantities exact.
A value that must be whole, such as a trading denomination, fails validation if it is not, rather than being rounded.

Rows are validated column by column: one pydantic `TypeAdapter(list[T])` per field, built from the row model. For
100,000 securities this takes about 130ms; building a model per row was
measured about 5x slower. Validation stays on even in the hot path. The constraint is that field rules must live in the
annotation (`Annotated[float, Field(gt=0)]`). A `@field_validator` would silently not run, so the loader refuses models
that have one. The annotations also choose the pandas dtype. A `Literal` becomes an ordered categorical, which is why a
rule can write `rating <= "A-"`.

Caching is a property of the data, not of the code that reads it. A dataset's `CachePolicy` says how long its entry
lives, whether `optim warm` fills it, and whether a run may read the source when the entry is missing. The per-portfolio
datasets have no policy, so they are always read live. Every Job is a new process, so the cache is Redis, shared by all
of them. Nothing is cached in process memory: a job that needs a dataset twice loads it once and passes the frame, as
`Market` does for every portfolio in a run. Cache keys include the query parameters, the SQL text and the row schema.
The parameters include the business date, so each day starts with new entries, and a deploy that changes a query or a
schema never reads what the old code cached.

`optim warm` is the morning Job. It finds every declared dataset whose policy has a warm hook, asks the hook which
parameters to load it with, reads each set live, and overwrites its entry, so the day's runs read them from Redis. A
failed load does not stop the others, but it fails the Job, as Redis failing does, and so does a load that returns
no rows: an empty entry would hide the data for its whole ttl once the feed lands. Runs never cache an empty result
either.

A run never fails because of the cache, since the cache only saves time. An entry it cannot read, such as a corrupt
one, is a miss, and the live read replaces it. The first time Redis is unavailable (down, unreachable or refusing the
password), the run stops asking it, so an outage costs one timeout and one warning, not two per dataset. Each
dataset's policy decides what a miss means: read the source, or fail. A dataset that is too heavy to read once per Job
sets `live_on_miss=False`, so an outage cannot turn every Job into a full read of the source. Without Redis, or with
`--no-cache`, a run reads everything live.

### Loading at the pace of each source

In production a query can take minutes, so a run asks for everything it needs at the start and lets each source set
the pace. A `TaskGroup` from `data/loader.py` runs loads in threads: the market and the portfolios, and every batch of
portfolios. Each source has a limit, `OPTIM_<SOURCE>__MAX_CONCURRENT`, which the loader holds as a semaphore around the
source call alone, so cache hits and validation never wait for a slot. A run then takes about as long as its slowest
source, not the sum of its queries. Every `loaded` record carries `wait_ms`, the time spent waiting for a slot, and a
read-only transaction, which waits once for the slot its loads share, carries it on its `began read-only transaction`
record: a long wait means the limit, not the source, is the bottleneck. Limits are per process, so two Jobs running at once send twice
as many queries.

Reads that depend on each other share a read-only transaction. A portfolio's holdings, cash, controls and cash flows are
only consistent if they come from one moment: a trade booked between the read of its holdings and the read of its cash
would count the trade's cash but not its bonds, or the other way round. So `_load_batch` in `datasets/load.py` reads
each batch's per-portfolio datasets inside `Loader.read_only("mssql")`, which opens one connection, begins a read-only
transaction on it (snapshot isolation on SQL Server, `SET TRANSACTION READ ONLY` on Oracle), runs the batch's queries in
it, and rolls it back. The scope is explicit in the code: a read outside it opens its own connection and sees its own
moment. The transaction's queries take turns on its connection, since a DB-API connection is not safe to use from two
threads at once, and the transaction holds one of the source's slots throughout, so open connections never exceed the
limit. Batches still load concurrently, each in its own transaction: consistency matters within a portfolio, not across
portfolios. HTTP has no transactions, so a REST source's reads in a scope are each their own.

Every fetch is bounded. Rows come back from a cursor ten thousand at a time, and a query that returns more than
`OPTIM_<SOURCE>__MAX_ROWS` fails once it passes the limit, so a query that lost its filter fails fast instead of filling
the Job's memory. A REST reply is checked against the limit after it is read, so the limit bounds what is validated,
not what is downloaded.

The first failed load stops the rest: loads still waiting for a slot are skipped, and those already running end at
their source's timeout, `OPTIM_<SOURCE>__TIMEOUT_S`. A run with a bad batch fails in about one query's time, not
after every other batch has loaded.

Batched queries bind a list of ids as one JSON array string, which the SQL unpacks with `OPENJSON` (SQL Server) or
`JSON_TABLE` (Oracle). One bind per batch avoids Oracle's 1,000-item `IN` limit and keeps SQL out of string
formatting. Type what `OPENJSON` returns, as `WITH (id VARCHAR(64) '$')`: its untyped values are NVARCHAR, and
comparing them with a VARCHAR key scans the table. For one portfolio among two million positions, that is 54ms against
0.4ms.

## Preparing the inputs

Between the loader and the strategy's rules is prep: what a solve needs that combines datasets and is the same for
every strategy. Whether logic is prep or a rule turns on who chooses it, not on how much of the data it covers:

|  | Every strategy needs it | The strategy chooses it |
|---|---|---|
| **The whole market** | `build_market`, once a run, in the parent | a rule, such as `min_rating` |
| **One portfolio** | `build_portfolios`, and `inputs_for` in the worker | a rule that reads `inputs.portfolio` |

Logic about one dataset alone, such as a range or a check across its rows, belongs to its `Dataset` instead.

The test is that a strategy with no rules must still solve correctly and safely, and whatever that needs is prep. A
portfolio's controls are the clearest case (see [Controls are prep](#controls-are-prep-not-rules)). Rules can only
narrow what may be bought or sold, so a safeguard in prep holds for every strategy, where the same safeguard as a rule
would be lost from any strategy file that left it out.

`build_market` joins the per-security datasets into one row per security, turns prices into floats, and gives each row
its `unit_value` and an `expected_return`, since the solver needs a number for every security it holds.
`inputs_for` turns one portfolio into lines, its cash and its cash flows, and applies its controls (see
[Positions, cash and sleeves](#positions-cash-and-sleeves)). Prep fills gaps without erasing facts a rule may need:
after a fill, a rule cannot tell no signal from a signal of zero, so keep a column such as `has_signal` if one needs to.

Prep fails the run when shared data is wrong: a market dataset with no rows, a benchmark constituent without
analytics, or a portfolio id that is not found. A problem with one portfolio's data fails only that portfolio, in the
worker: a holding of a security without analytics or in a sleeve the portfolio does not have, a security in another
currency than the portfolio's, an eligibility list or benchmark that is not loaded, or controls that conflict.

Prep runs on replay too, and its tests build frames by hand: `tests/test_inputs.py` needs no loader or database.

## Positions, cash and sleeves

A portfolio is its holdings: what each of its sleeves holds of each security, by a canonical `security_id`. A CUSIP or
an ISIN is an identifier a security may also have, so a security with neither, such as a corporate-action position or a
private placement, is a security like any other. Cash is a holding too, of a security whose `asset_class` is `cash`.
Prep pools every cash holding into the portfolio's cash, whichever sleeve it was booked to, because the sleeves of a
portfolio are solved together and spend from one balance.

Cash can be negative, as when a purchase settles before the sale that funds it: the solve then has to raise it, since
cash after trading may not be. Any other holding must be positive, or prep fails the portfolio: short positions are not
modelled.

A sleeve is a part of a portfolio with its own eligibility list and benchmark, such as a corporate sleeve and a
Treasury sleeve. A product with one tradeable sleeve has one row per portfolio in `sleeves`; a two-sleeve product has
two, and both are solved in the same problem, coupled through the shared cash and any constraint on the whole
portfolio. An eligibility list says what a sleeve may buy. It is not inventory: a list can say how much of a security is
available, and the portfolio then buys no more of it across all its sleeves, at the least that any list offering it
gives; without that, it buys within what the portfolio can spend. A held security that is on no list is still held, and
can be sold.

The solve works on lines: one per sleeve and security, for every holding but cash and every security on a sleeve's
list. A line is `buyable` when it is on its sleeve's list, in the portfolio's currency, traded in round lots and not
controlled; `sellable` when it is held and not controlled. Rules narrow those two flags, line by line.

Every value is a quantity times its security's unit value, `(price + accrued) / price_per`, in the portfolio's
currency. Price and accrued interest are separate columns, and `price_per` says what quantity a price is for: 100 for a
bond quoted in percent of par, 1 for cash. Nothing assumes a value is par times one price over 100. A portfolio has one
currency, and there are no FX rates: a security in another currency cannot be bought, and holding one fails the
portfolio.

Weights are values over a base value, and constraints and terms are written in weights so that they share one
tolerance. A portfolio's base value is its `target_value` where one is set, as for a portfolio being funded to an
agreed size, and otherwise its value after cash flows. A sleeve's base value is its target weight times the portfolio's,
or, without a target, its value before trading. A constraint or term covers a scope, the whole portfolio or one sleeve
(`"sleeve": "credit"`), and its weights are fractions of that scope's base value. A position limit of 5% in a credit
sleeve targeted at 60% caps a bond at 3% of the portfolio. Base values are constants, so a weight is linear in the
trades, and the problem stays an LP or a MIP.

Benchmark weights follow the same convention. A sleeve's benchmark, whose weights sum to 1, is its target in its own
weights. The portfolio's benchmark is the sum of its sleeves', each times the sleeve's share of the portfolio's base
value; what the shares leave, such as cash, has no benchmark. An exposure, such as duration, is each line's weight times
its value of the analytic, summed, so cash counts as zero, and a benchmark-relative exposure compares it with the same
sum over the scope's benchmark constituents, held or not. A constraint or term measured against a benchmark fails a
scope whose benchmark weighs nothing, rather than measuring it against zero: the whole portfolio's does when no sleeve
with a benchmark has a target weight or holdings, as for a newly funded portfolio.

External cash flows are a dataset of their own: a contribution adds to the cash the solve may invest, and a withdrawal
is cash it must raise, since cash after trading and flows may not go below zero. A request's `holdings` says which
holdings a portfolio starts from: `working`, the start of the day plus what has traded since, for intraday runs, or
`sod`, the start of the day's, for a start-of-day batch. The holdings query decides what each means in the books.

## Controls are prep, not rules

A portfolio's controls are mandatory: what may not be bought (`no_buy`), what may not be sold at the optimizer's
discretion (`no_sell`), what is held constant (`hold`), and what must be sold (`must_sell`). A control can name one
security or, with no security, every security in the portfolio, as for an account that is closing. Wash-sale
restrictions arrive this way, as `no_buy` controls the source system computes.

Controls are applied in prep, in `apply_controls` (`prep/controls.py`), which `inputs_for` calls, because a strategy
that omitted a rule must not trade around a restriction. For the same reason, a control that cannot be applied fails the
portfolio rather than being skipped. A control that names a security the market does not have fails it: that is how an
identifier mismatch would otherwise make a restriction silently match nothing. So does a pair of controls that both
require and forbid a sale, such as `must_sell` and `no_sell` on one security: the solve does not choose between them,
and the error quotes both with their reasons. A mandatory sale of a security the portfolio no longer holds has happened,
as in a working run after its trade: it logs a warning and still stops the security being bought back. A source that
cannot be read fails the run, and an empty answer is read as "no controls", so the query must not hide a failure as no
rows.

A mandatory sale and a discretionary block are handled differently on purpose. `no_sell` and a rule's
`restrict_sales` stop the optimizer choosing to sell. `must_sell` sells the whole holding, exactly as booked, and buys
none of it back, and no rule can block it: `restrict_sales` leaves a mandatory sale sellable.

## Strategies are JSON, and the JSON schema is the code

A `Strategy` is a list of rules, a list of objective terms, a list of constraints, and a `solve` block that says how
to solve and when an answer may trade. Each rule, term and constraint is a small pydantic class whose fields are its
parameters and whose method does the work: `apply` for a rule, `expression` for a term, `build` for a constraint. Rules
are grouped by family in `rules/`, constraints in `optimizer/constraints/`, and terms in `optimizer/objective/`.
Each kind is collected into a discriminated union on `kind`. A strategy file therefore validates strictly, an unknown
`kind` or a typo'd field is rejected, and `optim schema` prints a JSON schema that traders' tools and agents can write
against. There is no registry to keep in sync: the union is the registry. Learning one of the three teaches the other
two.

A strategy is also refused when it is read if its solve cannot solve it as written: a constraint that counts trades
with an LP solve, or a quadratic objective term with an integer solve on a solver that cannot do both, such as HiGHS.
The engine never makes such a strategy solvable by changing it: it does not switch solvers, and it does not replace a
quadratic term with a linear approximation.

Rules never drop rows. They narrow what may be bought or sold, by line (`SolverInputs.restrict_buys`,
`restrict_sales`), or adjust what the solver believes (`expected_return`). A security that a rule forbids buying may
still be held, and the solver must be able to sell it; a line that a rule forbids selling is still held, and counts
toward every limit. Prices, analytics and holdings are facts the solve reads as they are, so rules leave them alone.

## The model and the solve

The model decides what is bought and sold per line. The objective is the sum of the strategy's terms, each a fraction
of base value; by default, expected return over a horizon less trading costs. Terms can be linear, such as expected
return, piecewise linear, such as L1 tracking, or quadratic, such as quadratic turnover, exposure tracking and ladder
balance. Every constraint builds from numpy and sparse scipy arrays. Group limits, for example, are one sparse
membership matrix times the weight vector, which keeps cvxpy's compile time flat as the universe grows.

The model holds variables only for lines that can be bought or are held, and sale variables only for lines that can be
sold. Benchmark constituents outside that set add a constant to tracking error and are left out without changing the
answer. So the size of a solve, and its time, follows the buyable set: rules that narrow it make every solve faster. A
portfolio with nothing it may buy or sell, such as one under a blanket `hold`, has no variables, and is not sent to a
solver: its result, no trades, is reviewed like any other, so a frozen portfolio short of cash is still rejected.

A strategy's `solve` block chooses between two ways to solve. An integer solve, the default, trades exactly what the
market trades. Each security's data gives a minimum piece, an increment and a minimum remaining; a security without
them, such as a corporate-action position, cannot be bought and is only sold whole. A buy is nothing, or whole
increments of at least the minimum piece. A holding is sold whole, exactly as booked, which is how an odd amount and a
mandatory sale go, or in part: whole increments of at least the minimum piece, leaving at least the minimum remaining.
The model counts increments in integer variables and gives each trade a 0/1 flag, which constraints can count too.
Solvers return integers as values such as 2.9999999, so `optimizer.rounding` snaps them to exact integers after the
solve.

An LP solve trades fractional amounts, and is tens to hundreds of times faster. Its variables are weights, not
quantities, so the solver sees numbers near 1: given quantities in the millions, HiGHS's QP solver stops at its
iteration limit or takes zero for optimal. After the solve, `optimizer.rounding` rounds buys down and sales up, both
toward cash, so position limits, group limits and the cash floor survive. Turnover and other bounds may not, and
acceptance rejects such a result.

The solver is the strategy's choice: HiGHS, free, solves LPs, MIPs and QPs without integers; COPT, licensed, also
solves mixed-integer QPs. Each has a backend in `optimizer/backends.py` that maps the time limit and its defaults to the
solver's own options, and reads its own status into a `Termination`: whether it proved optimality, stopped at a limit,
or doubts its answer, whether it has a solution, its gap and its node count. cvxpy's status is not enough: it reports
COPT stopped by its time limit as `optimal_inaccurate` with a solution and `infeasible_inaccurate` without one. Every
solve has a time limit, 30 seconds by default. One that reaches it with a solution logs a warning, and acceptance judges
its gap; one with none is unsolved.

## Acceptance: when a result may trade

A solver that reports optimal has solved the model it was given, within its tolerances. That is not permission to
trade: rounding may have broken a constraint, a time limit may have left a poor answer, and a formulation can count
cash differently from the books. So `optimizer.acceptance.review` rebuilds every result from its exact trades, apart
from the model, and checks it before it is saved. Each trade's quantity is exact: whole units, or for a sale of a whole
holding the quantity as booked. The holdings and cash after trading, and every dollar in the summary, are computed
from those trades and the inputs alone.

A result is accepted only if every check passes. Its trades are sizes the market trades, nothing is bought or sold that
a control or rule forbids, every mandatory sale is whole, cash after trading is not negative, the model's cash and
holdings agree with the rebuilt ones, an integer answer is within the strategy's `max_gap` of the best possible,
whether the solver called it optimal or stopped at its time limit, and each of the strategy's constraints holds at the
exact trades. `max_gap` judges optimal answers too because a solver calls an answer optimal once it is within its own
gap option, which a strategy's `options` can loosen. A rejected result is saved with the checks it failed,
in `violations.parquet`, and makes the run exit nonzero, so nothing downstream mistakes it for a tradable one.
[Snapshot files](snapshots.md#results) lists the checks.

## Jobs and replay

A job is its request class, and jobs differ only in the solves they ask for. `optimize` solves real portfolios, one
solve each, from working or start-of-day holdings. Another job asks for other tasks from the same engine: a
transition analysis, for example, would solve a legacy portfolio once per turnover budget, each a strategy variant with
its own name, and its tasks would solve in parallel like any batch.

`replay` runs any job again from its snapshot. It reads the saved request and datasets, prepares them with the code you
have now, and asks the request for its solves as the live run did, with the saved strategy or another one. It saves the
result as a new run of the same job, recording the run it replayed, so a replay can be replayed too. With the saved
strategy, a replay shows whether a code change moved any result; with another, it compares strategies on the same
data, without database access. A solve that stops at its time limit may stop elsewhere on another machine, so its
replay can differ.

## Logs are written for agents

Every record is one JSON object per line on stderr, because the main reader is an agent, and Azure Monitor parses the
same lines; `OPTIM_LOG_FORMAT=text` is for a person at a terminal. Logging lives in the shared parts, not in jobs or
rules: the loader, the SQL and HTTP adapters, the engine and the solver each log what passes through them, so a new
dataset, rule or job is logged without a line of logging code. At INFO a run tells its story: what loaded, each
portfolio's result, trades and failed checks, and the snapshot, with a record for every load (with its cache outcome),
query (connect, execute and fetch times, and whether it ran in a transaction), HTTP call and solve (size, settings, the
solver's stop and own status, gap, nodes, and build, compile and solver times), for every read-only transaction begun
(the wait for its slot), and for the rules before each solve (each rule's time). `OPTIM_LOG_LEVEL=DEBUG` adds a record
for every rule (what it made unbuyable, how many lines it left sellable, and how many returns it changed).

Every record of a run carries its `run_id`, including the run's failure, so one filter finds the whole run, even one
that died before writing its snapshot. The snapshot itself is `OPTIM_SNAPSHOT_DIR/<run_id>`.

Records stay small, because AKS splits lines over 16KB. Bulk data goes in the snapshot: the logs say what happened, and
the snapshot holds what the run read and returned. A long list of ids in a record, such as a run's portfolios, is cut to
its size and first few ids. Library warnings are records too.

An exception is logged once, where its traceback is whole: by the CLI, or in the worker for a solve, so one portfolio's
failure is logged and the batch goes on. The record carries the error, the notes added on the way up (the loader names
the dataset and its parameters; an HTTP error carries the server's reply), the root cause, and `where`: the line in
this package where it surfaced, even when a library raised it.

## Known limits and assumptions

These are deliberate shortcuts, and conventions the placeholder model assumes until a port confirms or changes them.

- **Integer solve times.** HiGHS is free, and slower than commercial solvers. Integer solves take seconds to tens of
  seconds per portfolio where an LP takes a fraction of a second, and a large portfolio against a broad target may find
  no solution within the time limit. Name COPT in the strategy where it is licensed (see
  [How to configure a solve](extending.md#how-to-configure-a-solve)); otherwise solve such portfolios with
  `"integer": false`.
- **LP rounding.** Rounding after an LP breaks constraints the LP made binding, so its results are often rejected.
  Use an integer solve when the result must trade.
- **Minimum pieces** are assumed to be whole numbers of increments, as they are for nearly every bond. A minimum piece
  plus whole increments is exact for the rest, and measured about 1.7x slower.
- **Name-level tracking.** An L1 penalty against a 50,000-name index asks a $1mm portfolio for thousands of positions
  below the minimum piece. Rounding erases them in an LP solve, and an integer solve has to search among them. For
  small portfolios, track exposures instead, with `exposure` constraints or the `exposure_tracking` term.
- **One currency per portfolio.** There are no FX rates, so every value is in the portfolio's currency.
- **Cash is pooled.** The sleeves of a portfolio share one cash balance, and a sleeve's base value excludes cash.
- **Trading costs are reserved in cash.** Estimated costs come off cash after trading, so the cash floor holds at
  prices up to that estimate worse than the mark.
- **Controls apply to a security in every sleeve.** A control names a security, not a line, and a mandatory sale is
  of the whole holding. A partial mandatory sale needs a quantity on the control.
- **Inventory is per portfolio.** An eligibility list's `available` bounds each portfolio's buys of a security, across
  its sleeves, alone; portfolios that compete for the same offering are not allocated between.
- **Holdings are read from the primary.** Working holdings must include the trades just booked, so the SQL Server
  connection must not be routed to an asynchronous readable secondary (`ApplicationIntent=ReadOnly`), whose lag could
  make a run propose a trade again.
- **Consistency is per batch.** A batch's per-portfolio datasets are read in one transaction, but the market and the
  batches are each their own moment.
- **Snapshots** write the whole market every run, to a local directory. Store the market once per content hash if
  storage grows too fast.
