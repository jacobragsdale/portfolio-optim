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
5. Load it in `src/optim/inputs.py`. Per-security data goes in `load_market`: add it to the `loader.load_all(...)`
   call and merge `frames["issuer_scores"]` into `securities` with `validate="many_to_one"` or `"one_to_one"`. A rule
   or constraint can then read the new column from `inputs.securities` or `model.securities`.

   Per-portfolio data goes in `load_portfolios` through `loader.load_batched`, which sends up to 1,000 portfolios per
   query; the SQL unpacks the ids with `OPENJSON` or `JSON_TABLE`, as `lots.sql` does, and `loss_sales.sql` binds the
   business date too. Split the frame with `by_portfolio`, as `lots` and `loss_sales` are, then carry it to the solve: a
   field on `Portfolio` and on `Inputs`, a line in `Market.inputs_for`, and an empty frame for the all-cash portfolio in
   `jobs/model_portfolio.py`. Add it to `input_frames` in `outputs/snapshots.py` and read it back in `load_run`: a run
   whose snapshot lacks it still solves, but cannot be replayed.

## How to cache a dataset

Give the `Dataset` a policy, such as `cache=CachePolicy(ttl=timedelta(hours=12), warm=by_date)`. Every Job shares the
cache through Redis. An entry's key is the dataset, its SQL text, its row schema and every parameter it was loaded
with, so any query can be cached, whatever it binds. The policy has three settings:

- `ttl`: how long an entry lives in Redis.
- `warm`: a hook that returns the parameters `optim warm` loads the dataset with, one entry each. `optim warm` reads
  each one live and overwrites its entry. Schedule it in Airflow before the day's first run; `optim warm --datasets
  NAME ...` warms only the named datasets, for example on a faster schedule or after a data correction. Without a
  hook, the first load of each key reads the source and fills the entry.
- `live_on_miss=False`: a run fails instead of reading the source when the entry is missing or Redis is down. Use it
  for queries too heavy to run once per Job. It requires a `warm` hook, because nothing else fills the entry.

A hook takes the loader and the business date, and returns one dict of parameters per entry. `by_date` covers a query
that binds only `as_of`. Anything else is a short function:

```python
def _by_curve(loader: Loader, as_of: date) -> list[dict[str, Param]]:
    return [{"as_of": as_of, "curve": curve} for curve in ("ust", "sofr")]


def _by_portfolio(loader: Loader, as_of: date) -> list[dict[str, Param]]:
    return [{"as_of": as_of, "portfolio_id": i} for i in loader.load(PORTFOLIOS, as_of=as_of)["portfolio_id"]]
```

A job reads a warmed entry only if it loads the dataset with exactly the parameters the hook returned. Any other
parameters are another key. A warmed dataset that misses logs a warning with its parameters, so a mismatch shows in
the run's logs, and at DEBUG every load logs its parameters and whether it hit the cache.
`load_batched` caches each batch under its own key, so it only reads an entry back for the same batch of ids. A deploy
that changes a dataset's SQL or row schema changes its keys, so run `optim warm` after it. `optim clear --datasets
NAME ...` deletes every entry of the named datasets, whatever their parameters, say after a data correction; until
they are filled again, runs read them live, or fail where `live_on_miss=False`. If one warm load fails, the
rest still land, and the Job exits 1; a load that returns no rows counts as failed, since runs never cache an empty
result. Runs without Redis (`OPTIM_REDIS_URL=none`) or with `--no-cache` read every
dataset live, whatever its policy.

## How to add a rule

1. Pick the module in `src/optim/rules/` for the rule's family (`eligibility`, `tax`), or add one.
2. Write a `Strict` class with a `kind: Literal["your_rule"] = "your_rule"` field, its parameters as fields, and
   `apply(self, inputs: Inputs) -> Inputs`.
3. Narrow buying with `inputs.restrict(allowed)`, or return `replace(inputs, securities=inputs.securities.assign(...))`
   to adjust a column. Never drop rows.
4. Add the class to the `Rule` union in `src/optim/rules/__init__.py`.

Strategy files can use the rule as soon as it is in the union.

## How to add a constraint

1. Pick the module in `src/optim/optimizer/constraints/` for the constraint's family (`limits`, `risk`, `trading`,
   `tax`), or add one.
2. Write a `Strict` class with a `kind` field, its parameters, and `build(self, m: Model) -> list[cp.Constraint]`.
3. Write the constraint against the model's expressions: `m.weights`, `m.cash`, `m.turnover`, `m.tax`, or a column of
   `m.securities` times the weights, such as `m.securities["duration"].to_numpy() @ m.weights`. `m.inputs` holds the
   portfolio and its lots. Divide dollar quantities by `m.value` so the constraint is in weights and shares the tolerance
   of the re-check after the solve.
4. If you need a quantity the model lacks, add it to `Model` in `src/optim/optimizer/model.py` and compute it once in
   `build_model`.
5. Add the class to the `Constraint` union in `src/optim/optimizer/constraints/__init__.py`.

A constraint that counts trades or positions needs an integer solve. Count with the model's 0/1 flags, `m.buying` per
bond and `m.selling` per lot, as `max_buys` does; they are `None` in an LP solve, so raise a clear error then. A
constraint can also declare its own 0/1 variables and tie them to what it limits. This one caps the bonds held after
trading: a weight can only be above zero where its flag is 1, and no weight exceeds 1.

```python
class MaxHoldings(Strict):
    """Hold at most `n` bonds after trading. Needs an integer solve."""

    kind: Literal["max_holdings"] = "max_holdings"
    n: int = Field(gt=0)

    def build(self, m: Model) -> list[cp.Constraint]:
        held = cp.Variable(len(m.securities), boolean=True)
        return [m.weights <= held, cp.sum(held) <= self.n]
```

## How to add an objective term

A strategy's objective is the sum of its terms, listed under `"objective"`. Without that key, a strategy maximizes
expected return less tax and trading costs.

1. Write a `Strict` class in `src/optim/optimizer/objective.py` with a `kind` field, its parameters, and
   `expression(self, m: Model) -> cp.Expression`.
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

Every run's snapshot holds the market, portfolios and lots it loaded. `optim replay` solves them again with another
strategy, so two strategies can be compared on the same data:

```sh
uv run optim replay snapshots/RUN_ID --strategy strategies/my-variant.json --workers 4
```

Replay reads only the snapshot directory. It needs no `.env`, database or Redis, and writes nothing but its own snapshot. It
applies the new strategy's rules, objective, constraints and solve block, saves the results as a new run beside the
original, and prints the summary. `--portfolios A1 A2` replays only some portfolios. Each run's `summary.parquet` holds one
row per portfolio, so comparing two runs is a join on `portfolio_id`.

## How to add a data source

1. Add a module to `src/optim/sources/`, such as `pricing.py`, with a settings model (a `Model` subclass) and a
   `connect` function. A database with a DB-API driver returns a `Sql` over a connection factory; anything else
   returns a class with `fetch(self, query, params) -> Columns`. Put a timeout on every call, the login included:
   Oracle's thin driver bounds only the TCP connect, so `oracle.py` runs its login in a thread it stops waiting for.
2. Add the source's name to `SourceName` in `src/optim/sources/__init__.py`.
3. Add its settings to `Settings` in `src/optim/settings.py`, and its variables to `.env.example`.
4. Open it in `open_context` in `src/optim/context.py`. If it holds a pool or client, enter it on the `ExitStack` so it
   closes.

If the source is another JSON API, it needs only steps 2 to 4: reuse `ApiSettings`, `rest.client` and `Rest`.

## How to add an entry point

1. Add a module to `src/optim/jobs/` with a request model (a `Model` subclass) and a function taking
   `(ctx: Context, request: YourRequest)`. First take a run id from `ctx.new_run_id()` and `logs.bind(run_id=...)`
   it, so every record of the run carries it. Load with `load_market` and `load_portfolios`, build `Task`s, call
   `solve_all`, and save the run with `save_run` before any other output.
2. Add a subcommand to `_parser()` in `src/optim/cli.py` that builds the request, and a `case` for it in `main`.

## How to add an output

A report, a write to a database or a call to another API after the solves:

1. Add a module to `src/optim/outputs/`, such as `reports.py`, with a function that takes what it needs: the `run_id`,
   the results (`result_frames(results)` gives the trades, positions and summary as frames), and a client or
   connection.
2. If it needs a client or connection, add its settings and open it in `open_context` as for
   [a data source](#how-to-add-a-data-source), and add it to `Context`.
3. Call it from each job that should produce it, after `save_run`, so a failed output never loses the run's snapshot.
