# Extending the optimizer

Each kind of extension is one new module, or one new class in an existing module, plus a line where it is collected.
None needs a registry or factory. Run `uv run pre-commit run --all-files` after each change.

## How to add a dataset

To load a new table, for example issuer credit scores from Oracle:

1. Pick the module in `src/optim/datasets/` for its subject, or add one, such as `issuers.py`.
2. Write its row model there. Field names are the column names the query returns. Put every field rule in the
   annotation, such as `score: Annotated[float, Field(ge=0, le=100)]`, because rows are validated column by column.
3. Add `src/optim/datasets/sql/issuer_scores.sql`. Alias each column to its field name. Bind parameters as `:name` for
   Oracle or `%(name)s` for SQL Server.
4. Declare the dataset beside its row model:
   `ISSUER_SCORES = Dataset("issuer_scores", IssuerScore, source="oracle", query="datasets/sql/issuer_scores.sql", key=("issuer",))`.
   Without a `cache`, it is read live on every load. To cache it, see the next section.
5. Load it and prepare it in `src/optim/inputs.py`. Per-security data is loaded once a run: add the dataset to
   `MARKET`, which also fails the run if it returns no rows. Every `MARKET` dataset is loaded with the business date, so
   its SQL must bind `:as_of_date`: Oracle refuses a bind its SQL does not use. A REST source receives it as a query
   parameter. Merge `frames["issuer_scores"]` in `build_market` with
   `validate="many_to_one"` or `"one_to_one"`. A rule or constraint can then read the new column from
   `inputs.securities` or `model.securities`.

   Per-portfolio data is loaded in batches: in `_load_portfolios`, submit a `loader.load_batched` call to its
   `TaskGroup`, so it loads beside lots and loss sales, and return its frame with theirs. `load_batched` sends up to
   1,000 portfolios per query, as many batches at once as the source's limit allows; the SQL unpacks the ids with
   `OPENJSON` or `JSON_TABLE`, as `lots.sql` does, and `loss_sales.sql` binds the business date too. Then carry it to
   the solve: a field on `Portfolio`, filled in `build_portfolios` as `lots` is. Rules, constraints and terms read it as
   `inputs.portfolio.<field>` or `m.inputs.portfolio.<field>`.

   Snapshots, replay and the all-cash model portfolio need no change: a snapshot saves every frame `load_frames`
   returns, replay prepares them as a live run does, and a model portfolio loads empty portfolio frames. A snapshot
   saved before the dataset existed cannot be replayed.

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
the run's logs, and every load logs its parameters and whether it hit the cache.
`load_batched` caches each batch under its own key, so it only reads an entry back for the same batch of ids. A deploy
that changes a dataset's SQL or row schema changes its keys, so run `optim warm` after it. `optim clear --datasets
NAME ...` deletes every entry of the named datasets, whatever their parameters, say after a data correction; until
they are filled again, runs read them live, or fail where `live_on_miss=False`. If one warm load fails, the
rest still land, and the Job exits 1; a load that returns no rows counts as failed, since runs never cache an empty
result. Runs without Redis (`OPTIM_REDIS_URL=none`) or with `--no-cache` read every
dataset live, whatever its policy.

## How to add a rule

1. Pick the module in `src/optim/rules/` for the rule's family (`eligibility`, `tax`), or add one.
2. Write a `StrictModel` class with a `kind: Literal["your_rule"] = "your_rule"` field, its parameters as fields, and
   `apply(self, inputs: SolverInputs) -> SolverInputs`.
3. Narrow buying with `inputs.restrict(allowed)`, or return `replace(inputs, securities=inputs.securities.assign(...))`
   to adjust a column. Never drop rows.
4. Add the class to the `Rule` union in `src/optim/rules/__init__.py`.

Strategy files can use the rule as soon as it is in the union.

## How to add a constraint

1. Pick the module in `src/optim/optimizer/constraints/` for the constraint's family (`limits`, `risk`, `trading`,
   `tax`), or add one.
2. Write a `StrictModel` class with a `kind` field, its parameters, and `build(self, m: OptimizationModel) -> list[cp.Constraint]`.
3. Write the constraint against the model's expressions: `m.weights`, `m.cash`, `m.turnover`, `m.tax`, or a column of
   `m.securities` times the weights, such as `m.securities["duration"].to_numpy() @ m.weights`. `m.inputs` holds the
   portfolio and its lots. Divide dollar quantities by `m.value` so the constraint is in weights and shares the tolerance
   of the re-check after the solve.
4. If you need a quantity the model lacks, add it to `OptimizationModel` in `src/optim/optimizer/model.py` and compute it once in
   `build_model`.
5. Add the class to the `Constraint` union in `src/optim/optimizer/constraints/__init__.py`.

A constraint that counts trades or positions needs an integer solve. Count with the model's 0/1 flags, `m.buying` per
bond and `m.selling` per lot, as `max_buys` does. Give the class `integer_only: ClassVar[bool] = True`, so a strategy
that solves an LP is refused when it is read, and still raise a clear error if a flag is `None`. A
constraint can also declare its own 0/1 variables and tie them to what it limits. This one caps the bonds held after
trading: a weight can only be above zero where its flag is 1, and no weight exceeds 1.

```python
class MaxHoldings(StrictModel):
    """Hold at most `n` bonds after trading. Needs an integer solve."""

    kind: Literal["max_holdings"] = "max_holdings"
    n: int = Field(gt=0)

    def build(self, m: OptimizationModel) -> list[cp.Constraint]:
        held = cp.Variable(len(m.securities), boolean=True)
        return [m.weights <= held, cp.sum(held) <= self.n]
```

## How to add an objective term

A strategy's objective is the sum of its terms, listed under `"objective"`. Without that key, a strategy maximizes
expected return less tax and trading costs.

1. Write a `StrictModel` class in `src/optim/optimizer/objective.py` with a `kind` field, its parameters, and
   `expression(self, m: OptimizationModel) -> cp.Expression`.
2. Return what the term adds to the objective as a fraction of portfolio value: divide dollars by `m.value`. A reward
   is positive and a penalty negative, as in `Tax`.
3. Keep the term linear, or piecewise linear like `cp.norm1`, which cvxpy turns into extra variables and rows: HiGHS
   solves linear problems.
4. Add the class to the `Term` union at the bottom of the module.

## How to configure a solve

Each strategy's `"solve"` block says how its portfolios are solved:

```json
"solve": {"integer": true, "solver": "HIGHS", "time_limit_s": 30, "options": {"mip_rel_gap": 0.001}}
```

- `integer`: `true`, the default, trades exactly the sizes the market trades. Each bond's `min_piece`, `increment` and
  `min_remaining` come from `datasets/sql/securities.sql`. `false` solves an LP and rounds its trades afterwards. That
  is tens to hundreds of times faster, but rounding can break a binding constraint, and a result with a broken
  constraint makes the run exit nonzero.
- `time_limit_s`: per portfolio. An integer solve that reaches it keeps the best solution it found, logs a warning,
  and reports how far that may be from the best possible as `gap` in the run's summary. If it found none, the portfolio
  is unsolved, with status `user_limit`.
- `options`: passed to the solver as they are, under the solver's own names, such as HiGHS's `mip_rel_gap` (default
  1e-4) or `presolve`.
- `solver`: `HIGHS`. To use another solver cvxpy supports, such as a licensed COPT, install its package (`uv add
  coptpy`), add its name to `solver` in `src/optim/strategy.py`, and map the time limit and the solver stats that
  `src/optim/optimizer/solve.py` reads.

## How to compare strategies on a saved run

Every run's snapshot holds its request and every dataset it loaded. `optim replay` runs the same job again on them,
with the code you have now and, with `--strategy`, another strategy, so two strategies can be compared on the same data:

```sh
uv run optim replay snapshots/RUN_ID --strategy strategies/my-variant.json --workers 4
```

Without `--strategy`, it replays the run as it was, which shows whether a code change moved any result. Replay reads
only the snapshot directory. It needs no `.env`, database or Redis, and writes nothing but its own snapshot. It
applies the strategy's rules, objective, constraints and solve block, saves the results as a new run of the same job
beside the original, and prints the summary. `--portfolios A1 A2` replays only some portfolios. Each run's
`summary.parquet` holds one row per solve, so comparing two runs is a join on `portfolio_id`; for a transition, which
has a row per tax budget, on `portfolio_id` and row order. [Snapshot files](snapshots.md) defines every column.

## How to add a data source

1. Add a module to `src/optim/sources/`, such as `pricing.py`, with a settings model (a `Model` subclass) and a
   `connect` function. Its settings model subclasses `SourceSettings`, so it has a concurrency limit and a timeout
   like every other source. A database with a DB-API driver returns a `SqlSource` over a connection factory; anything else
   returns a class with `fetch(self, query, params) -> Columns`. Put a deadline on every call: `settings.timeout_s` on
   each query or request, and a fixed one on the login, as `LOGIN_TIMEOUT_S` in `mssql.py` and `CONNECT_TIMEOUT_S` in
   `oracle.py` are. Oracle's thin driver bounds only the TCP connect, so `oracle.py` runs its login in a thread it stops
   waiting for.
2. Add the source's name to `SourceName` in `src/optim/sources/__init__.py`.
3. Add its settings to `Settings` in `src/optim/settings.py` under that same name, which `open_context` uses to find its
   limit, and its variables to `.env.example`.
4. Open it in `open_context` in `src/optim/context.py`, which also gives it its slot limit. If it holds a pool or
   client, enter it on the `ExitStack` so it closes.

If the source is another JSON API, it needs only steps 2 to 4: reuse `ApiSettings`, `rest.client` and `RestSource`.

## How to add a job

1. Add a module to `src/optim/jobs/` with a request class, a `Model` subclass. Give it a
   `job: Literal["your_job"] = "your_job"` field, a `strategy`, an `as_of_date` and whatever else it needs, then:
   - `portfolio_ids`, a property: the portfolios to load, or none.
   - `tasks(self, frames: Frames) -> list[SolveTask]`: the solves it makes from the loaded datasets. Build its
     portfolios with `build_portfolios(frames, ids)`, as `optimize.py` does.
2. Add the class to the `Job` union in `src/optim/jobs/__init__.py`. `run` then loads, prepares, solves and saves it,
   and `optim replay` replays its snapshots, with nothing else to write.
3. Add a subcommand to `_parser()` in `src/optim/cli.py`, and a `case` in `_run` that builds the request.

## How to add an output

A report, a write to a database or a call to another API after the solves:

1. Add a module to `src/optim/outputs/`, such as `reports.py`, with a function that takes what it needs: the `run_id`,
   the results (`result_frames(results)` from `optim.optimizer.results` gives the trades, positions, summary and
   violations as frames), and a client or connection.
2. If it needs a client or connection, add its settings and open it in `open_context` as for
   [a data source](#how-to-add-a-data-source), and add it to `Context`.
3. Call it in `run` in `src/optim/jobs/__init__.py`, after `_solve` returns: the snapshot is on disk by then, so a
   failed output never loses it. An output for one job checks `job.job`. Replays do not call it, so a replay never
   sends a report or writes a row.
