# Extending the optimizer

Each kind of extension is one new module, or one new class in an existing module, plus a line where it is collected.
None needs a registry or factory. Run `uv run pre-commit run --all-files` after each change.

## How to add a dataset

To load a new table, for example issuer credit scores from Oracle:

1. Pick the module in `src/optim/datasets/` for its subject, or add one, such as `issuers.py`.
2. Write its row model there. Field names are the column names the query returns. Put every field rule in the
   annotation, such as `score: Annotated[float, Field(ge=0, le=100)]`, because rows are validated column by column.
   Type a booked value, such as a price, a quantity or an amount of money, as `Decimal` (`Price`, `Quantity` in
   `optim.schema`), so it stays exact until prep, and key it to securities by `security_id`.
3. Add `src/optim/datasets/sql/issuer_scores.sql`. Alias each column to its field name, and do not cast a number to
   FLOAT: the drivers return exact decimals. Bind parameters as `:name` for Oracle or `%(name)s` for SQL Server.
4. Declare the dataset beside its row model:
   `ISSUER_SCORES = Dataset("issuer_scores", IssuerScore, source="oracle", query="datasets/sql/issuer_scores.sql", key=("issuer",))`.
   Without a `cache`, it is read live on every load. To cache it, see the next section.
5. Load it and prepare it in `src/optim/inputs.py`. Per-security data is loaded once a run: add the dataset to
   `MARKET`, which also fails the run if it returns no rows. Every `MARKET` dataset is loaded with the business date, so
   its SQL must bind `:as_of_date`: Oracle refuses a bind its SQL does not use. A REST source receives it as a query
   parameter. Merge `frames["issuer_scores"]` in `build_market` with `validate="many_to_one"` or `"one_to_one"`, and
   convert any `Decimal` column to float there. A rule or constraint can then read the new column from
   `inputs.lines` or `m.lines`.

   Per-portfolio data is loaded in batches of portfolios, each batch in one read-only transaction with the portfolio's
   other datasets, so they are read at one moment. Add the dataset to `PER_PORTFOLIO`, add a line for it to
   `_load_batch` with the parameters its SQL binds, such as `as_of_date=as_of_date`, and add a field to `Portfolio`
   named as the dataset is, which `build_portfolios` fills. The SQL unpacks the batch's ids with `OPENJSON`, as
   `holdings.sql` does. Rules, constraints and terms read it as `inputs.portfolio.<field>` or
   `m.inputs.portfolio.<field>`. A dataset from another source than SQL Server is loaded in its own scope: a transaction
   cannot span two databases.

   Snapshots and replay need no change: a snapshot saves every frame `load_frames` returns, and replay prepares them
   as a live run does. A snapshot saved before the dataset existed cannot be replayed.

## How to cache a dataset

Give the `Dataset` a policy, such as `cache=CachePolicy(ttl=timedelta(hours=12), warm=_by_date)`. Every Job shares the
cache through Redis. An entry's key is the dataset, its SQL text, its row schema and every parameter it was loaded
with, so any query can be cached, whatever it binds. The policy has three settings:

- `ttl`: how long an entry lives in Redis.
- `warm`: a hook that returns the parameters `optim warm` loads the dataset with, one entry each. `optim warm` reads
  each one live and overwrites its entry. Schedule it in Airflow before the day's first run; `optim warm --datasets
  NAME ...` warms only the named datasets, for example on a faster schedule or after a data correction. Without a
  hook, the first load of each key reads the source and fills the entry.
- `live_on_miss=False`: a run fails instead of reading the source when the entry is missing or Redis is down. Use it
  for queries too heavy to run once per Job. It requires a `warm` hook, because nothing else fills the entry.

A hook takes the loader and the business date, and returns one dict of parameters per entry. It is a short function
beside the dataset:

```python
def _by_date(loader: Loader, as_of_date: date) -> list[dict[str, Param]]:
    return [{"as_of_date": as_of_date}]


def _by_curve(loader: Loader, as_of_date: date) -> list[dict[str, Param]]:
    return [{"as_of_date": as_of_date, "curve": curve} for curve in ("ust", "sofr")]
```

A job reads a warmed entry only if it loads the dataset with exactly the parameters the hook returned. Any other
parameters are another key. A warmed dataset that misses logs a warning with its parameters, so a mismatch shows in
the run's logs, and every load logs its parameters and whether it hit the cache. A per-portfolio dataset is loaded a
batch at a time, so a cache entry would hold one batch, read back only for the same batch of ids: leave holdings,
controls and cash flows uncached. A deploy that changes a dataset's SQL or row schema changes its keys, so run
`optim warm` after it. `optim clear --datasets NAME ...` deletes every entry of the named datasets, whatever their
parameters, say after a data correction; until they are filled again, runs read them live, or fail where
`live_on_miss=False`. If one warm load fails, the rest still land, and the Job exits 1; a load that returns no rows
counts as failed, since runs never cache an empty result. Runs without Redis (`OPTIM_REDIS_URL=none`) or with
`--no-cache` read every dataset live, whatever its policy.

## How to add a rule

1. Pick the module in `src/optim/rules/` for the rule's family, such as `eligibility`, or add one. A rule is a
   choice a strategy makes; what every strategy needs, such as a portfolio's controls, belongs in prep
   ([preparing the inputs](architecture.md#preparing-the-inputs)).
2. Write a `StrictModel` class with a `kind: Literal["your_rule"] = "your_rule"` field, its parameters as fields, and
   `apply(self, inputs: SolverInputs) -> SolverInputs`.
3. Narrow buying with `inputs.restrict_buys(allowed)`, or selling with `inputs.restrict_sales(allowed)`, each one flag
   per line of `inputs.lines`: one sleeve's holding or candidate of one security, with that security's columns. A
   rule cannot block a mandatory sale: `restrict_sales` leaves it sellable. To change what the solver believes, return
   `replace(inputs, lines=inputs.lines.assign(expected_return=...))`. Never drop rows, and leave prices, analytics and
   holdings as they are.
4. Add the class to the `Rule` union in `src/optim/rules/__init__.py`.

Strategy files can use the rule as soon as it is in the union.

## How to add a constraint

1. Pick the module in `src/optim/optimizer/constraints/` for the constraint's family, such as `limits`, `exposures` or
   `trading`, or add one, such as `risk.py`.
2. Write a class with a `kind` field, its parameters, and `build(self, m: OptimizationModel) -> list[cp.Constraint]`.
   Subclass `Scoped` from `optim.optimizer.model` so a strategy can apply it to one sleeve with `"sleeve": "credit"`,
   or `StrictModel` for a constraint on the whole portfolio only.
3. Write the constraint in weights of its scope, `scope = m.scope(self.sleeve)`, against the model's expressions:
   `m.weights(scope)` per line, `m.exposure(scope, "duration")` and its benchmark's, `m.buy` and `m.sell` per line in
   quantity, `m.cash` and `m.turnover` in dollars, or a column of `m.lines` times the weights. Divide dollars by
   `scope.base` (`m.base` for the whole portfolio) so the constraint is in weights and shares the tolerance of the
   acceptance review: `(scope.members * m.unit_value / scope.base) @ (m.buy + m.sell)` is the scope's turnover.
4. If you need a quantity the model lacks, add it to `OptimizationModel` in `src/optim/optimizer/model.py` and compute
   it once in `build_model`. To measure exposure to a new numeric analytic, add the column to `Security` and its name
   to `Analytic` in `src/optim/datasets/securities.py`.
5. Add the class to the `Constraint` union in `src/optim/optimizer/constraints/__init__.py`.

A constraint that counts trades needs an integer solve. Count with the model's 0/1 flags, `m.buying` and `m.selling`
per line, which are `None` in an LP solve, and set `integer_only` so a strategy that pairs it with an LP solve is
refused when it is read. `MaxTrades` in `trading.py` is the example:

```python
class MaxTrades(Scoped):
    """At most `n` trades in the scope, buys and sales. Counts the integer solve's 0/1 flags, so it needs one."""

    kind: Literal["max_trades"] = "max_trades"
    n: int = Field(ge=0)
    integer_only: ClassVar[bool] = True

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        if m.buying is None or m.selling is None:
            msg = 'max_trades counts trades, which needs "solve": {"integer": true}'
            raise ValueError(msg)
        members = m.scope(self.sleeve).members.astype(float)
        return [members @ (m.buying + m.selling) <= self.n]
```

A constraint can also declare its own 0/1 variables and tie them to what it limits, such as a cap on the lines held
after trading: `held = cp.Variable(len(m.lines), boolean=True)`, then `m.weights(scope) <= held` and
`cp.sum(held) <= n`.

## How to add an objective term

A strategy's objective is the sum of its terms, listed under `"objective"`. Without that key, a strategy maximizes
expected return less trading costs.

1. Write a class in `src/optim/optimizer/objective.py` with a `kind` field, its parameters, and
   `expression(self, m: OptimizationModel) -> cp.Expression`. Subclass `Scoped` for a term a strategy can apply to one
   sleeve.
2. Return what the term adds to the objective as a fraction of base value: divide dollars by `m.base`, or by the
   scope's `base`. A reward is positive and a penalty negative, as in `Cost`.
3. A linear or piecewise-linear term, such as `cp.norm1`, which cvxpy turns into extra variables and rows, solves on
   every solver. A quadratic term, such as `cp.sum_squares` of weights, sets `quadratic: ClassVar[bool] = True`: HiGHS
   solves it only with `"integer": false`, and a strategy that asks for it with integer trades on HiGHS is refused
   when it is read. Write it in weights, as `TurnoverQuadratic` does, so its coefficients are near 1.
4. Add the class to the `Term` union at the bottom of the module.

## How to configure a solve

Each strategy's `"solve"` block says how its portfolios are solved, and when a solve's answer may trade:

```json
"solve": {"integer": true, "solver": "HIGHS", "time_limit_s": 30, "max_gap": 0.01, "options": {"mip_rel_gap": 0.001}}
```

- `integer`: `true`, the default, trades exactly the sizes the market trades. Each security's `min_piece`,
  `increment` and `min_remaining` come from `datasets/sql/securities.sql`. `false` solves an LP, or a QP with quadratic
  terms, and rounds its trades afterwards. That is tens to hundreds of times faster, but rounding can break a binding
  constraint, and acceptance then rejects the result.
- `solver`: `HIGHS`, free, or `COPT`, licensed. COPT solves quadratic objectives with integer trades, which HiGHS
  cannot. The image installs COPT and reads its license from `COPT_LICENSE_DIR` (`deploy/job.yaml` mounts the
  `copt-license` secret there); locally, `uv sync` installs it for tests. Without a license COPT runs size-limited, and
  refuses an integer problem over 2,000 variables or rows.
- `time_limit_s`: per portfolio. A solve that reaches it with a solution logs a warning and reports its `gap` in the
  run's summary; acceptance then judges it by `max_gap`. If it found none, the portfolio is unsolved, with status
  `limit with no solution`.
- `max_gap`: the acceptance policy for integer answers: one may trade only if it is within this of the best
  possible, as the solver reports its gap, whether the solver stopped at its time limit or called it optimal. A solver
  calls an answer optimal once it is within its own gap option, so set that option no looser than `max_gap`, or
  optimal answers are rejected. Default 0.01.
- `options`: passed to the solver as they are, under the solver's own names, such as HiGHS's `mip_rel_gap` (default
  1e-4) or `presolve`, or COPT's `RelGap` or `Threads`. They override the engine's defaults for the solver, such as
  COPT's one thread per solve.

To add another solver cvxpy supports, add a backend class to `src/optim/optimizer/backends.py` with its options and its
`termination`, which reads the solver's own status, gap and node count from `problem.solver_stats.extra_stats`, and an
entry in `SOLVERS`; add its package as an optional dependency in `pyproject.toml`. Strategy files can name it once it
is in `SolverName`.

## How to solve a portfolio with several sleeves

A portfolio's sleeves come from the `sleeves` dataset: one row per sleeve, with the eligibility list it buys from, its
benchmark and its target weight. Holdings name the sleeve they are booked to. All of a portfolio's sleeves are solved
in one problem and share its cash.

To give a product a corporate and a Treasury sleeve:

1. Make `sleeves.sql` return two rows per portfolio of the product, such as `credit` and `treasury`, each with its
   `universe_id`, `benchmark_id` and `target_weight`.
2. Make `universe.sql` return each sleeve's eligibility list under its `universe_id`, and `benchmarks.sql` each
   sleeve's benchmark under its `benchmark_id`.
3. Make `holdings.sql` return each position's `sleeve_id`. A holding in a sleeve the portfolio does not have fails
   the portfolio.
4. In the product's strategy file, give each constraint or term that applies to one sleeve its `"sleeve"`, and leave
   it out for one on the whole portfolio. `strategies/two-sleeves.json` is an example.

A sleeve's weights are fractions of its own base value: its target weight of the portfolio's base value, or its value
before trading. A strategy that names a sleeve a portfolio does not have fails that portfolio. For the conventions, see
[positions, cash and sleeves](architecture.md#positions-cash-and-sleeves).

## How to compare strategies on a saved run

Every run's snapshot holds its request and every dataset it loaded. `optim replay` runs the same job again on them,
with the code you have now and, with `--strategy`, another strategy, so two strategies can be compared on the same data:

```sh
uv run optim replay snapshots/RUN_ID --strategy strategies/my-variant.json --workers 4
```

Without `--strategy`, it replays the run as it was, which shows whether a code change moved any result. A label such
as `--label purpose=research` records why in the replay's `run.json`. Replay reads only the snapshot directory. It needs
no `.env`, database or Redis, and writes nothing but its own snapshot. It applies the strategy's rules, objective,
constraints and solve block, reviews each result, saves the results as a new run of the same job beside the original,
and prints the summary. `--portfolios A1 A2` replays only some portfolios. Each run's `summary.parquet` holds one row
per solve, so comparing two runs of `optimize` is a join on `portfolio_id`. [Snapshot files](snapshots.md) describes
every file.

## How to add a data source

1. Add a module to `src/optim/sources/`, such as `pricing.py`, with a settings model and a `connect` function. Its
   settings model subclasses `SourceSettings`, so it has a concurrency limit, a timeout and a row limit like every
   other source. A database with a DB-API driver returns a `SqlSource` over a connection factory, with the statement
   that begins a read-only transaction in its dialect and `settings.max_rows`; configure the driver to return exact
   decimals, as `oracle.py` does with `fetch_decimals`. Anything else returns a class with
   `fetch(self, query, params) -> Columns`, which refuses more than `max_rows` rows, and `read_only()`, which returns
   `nullcontext(self)` when the source has no transactions, as `RestSource` does. Put a deadline on every call:
   `settings.timeout_s` on each query or request, and a fixed one on the login, as `LOGIN_TIMEOUT_S` in `mssql.py` and
   `CONNECT_TIMEOUT_S` in `oracle.py` are. Oracle's thin driver bounds only the TCP connect, so `oracle.py` runs its
   login in a thread it stops waiting for.
2. Add the source's name to `SourceName` in `src/optim/sources/__init__.py`.
3. Add its settings to `Settings` in `src/optim/settings.py` under that same name, which `open_context` uses to find its
   limit, and its variables to `.env.example` and `deploy/job.yaml`.
4. Open it in `open_context` in `src/optim/context.py`, which also gives it its slot limit. If it holds a pool or
   client, enter it on the `ExitStack` so it closes.

If the source is another JSON API, it needs only steps 2 to 4: reuse `ApiSettings`, `rest.client` and `RestSource`.

## How to add a job

1. Add a module to `src/optim/jobs/` with a request class, a `Model` subclass. Give it a
   `job: Literal["your_job"] = "your_job"` field, a `strategy`, an `as_of_date` and whatever else it needs, then:
   - `portfolio_ids`, a property: the portfolios to load, or none.
   - `holdings`: which holdings they start from, `working` or `sod`, a field or a property.
   - `tasks(self, frames: Frames) -> list[SolveTask]`: the solves it makes from the loaded datasets. Build its
     portfolios with `build_portfolios(frames, ids)`, as `optimize.py` does. A job whose portfolios are not in the
     books, such as a prospect's or a model portfolio built from cash, loads none, adds their rows to the per-portfolio
     frames, and builds them the same way, as `hypothetical.py` does.
2. Add the class to the `Job` union in `src/optim/jobs/__init__.py`. `run` then loads, prepares, solves, reviews and
   saves it, and `optim replay` replays its snapshots, with nothing else to write.
3. Add a subcommand to `_parser()` in `src/optim/cli.py`, and a `case` in `_run` that builds the request.

A job can solve one portfolio more than once. A transition analysis, for example, solves a legacy portfolio once per
turnover budget: its `tasks` returns one `SolveTask` per budget, each with a copy of the strategy under its own name,
such as `Strategy.model_validate(base.model_dump() | {"name": "turnover-10", "constraints": [...]})`, and each result
is a row of the run's summary, told apart by `strategy`.

## How to add an output

A report, a write to a database or a call to another API after the solves, such as the proposals a start-of-day batch
publishes:

1. Add a module to `src/optim/outputs/`, such as `proposals.py`, with a function that takes what it needs: the
   `run_id`, the results (`result_frames(results)` from `optim.optimizer.results` gives the trades, positions, summary
   and violations as frames), and a client or connection. Publish only accepted results: a summary row whose `status`
   is `accepted`.
2. If it needs a client or connection, add its settings and open it in `open_context` as for
   [a data source](#how-to-add-a-data-source), and add it to `Context`.
3. Call it in `run` in `src/optim/jobs/__init__.py`, after `save_run` returns: the snapshot is on disk by then, so a
   failed output never loses it. An output for one job checks `job.job`, and one for start-of-day runs checks
   `job.holdings == "sod"`. Replays do not call it, so a replay never sends a report or writes a row. The engine never
   submits orders.
