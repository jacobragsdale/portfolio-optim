# Extending the optimizer

Each kind of extension lives in one file, and none needs a registry or factory. Run `uv run pre-commit run --all-files`
and `uv run pytest` after each change.

## How to add a dataset

To load a new table, for example issuer credit scores from Oracle:

1. In `src/optim/inputs.py`, write its row model. Field names are the column names the query returns. Put every field
   rule in the annotation, such as `score: Annotated[float, Field(ge=0, le=100)]`, because rows are validated column
   by column.
2. Add `src/optim/queries/issuer_scores.sql`. Alias each column to its field name. Bind parameters as `:name` for
   Oracle or `%(name)s` for SQL Server.
3. Declare it next to the other datasets:
   `ISSUER_SCORES = Dataset("issuer_scores", IssuerScore, source="oracle", query="issuer_scores.sql", key=("issuer",), ttl=timedelta(hours=1))`.
   Leave out `ttl` if the data must always be read live.
4. Load it where it belongs. Per-security data goes in `load_market`: add it to the concurrent `pool.map` and merge it
   into `securities` with `validate="many_to_one"` or `"one_to_one"`. Per-account data goes in `load_portfolios`
   through `loader.load_batched`, and the query unpacks the ids with `OPENJSON` or `JSON_TABLE`.
5. Add the dataset's records to `tables()` in `tests/builders.py` so every test still loads.

A rule or constraint can now read the new column from `inputs.securities` or `model.securities`.

## How to add a rule

1. In `src/optim/rules.py`, write a `Strict` class with a `kind: Literal["your_rule"] = "your_rule"` field, its
   parameters as fields, and `apply(self, inputs: Inputs) -> Inputs`.
2. Narrow buying with `_restrict(inputs, allowed)`, or return
   `replace(inputs, securities=inputs.securities.assign(...))` to adjust a column. Never drop rows.
3. Add the class to the `Rule` union at the bottom of the file.
4. Add a table-driven test to `tests/test_rules.py` covering the rule's boundary.

Strategy files can use the rule as soon as it is in the union.

## How to add a constraint

1. In `src/optim/lp.py`, write a `Strict` class with a `kind` field, its parameters, and
   `build(self, m: Model) -> list[cp.Constraint]`.
2. Write the constraint against the model's expressions: `m.weights`, `m.cash`, `m.turnover`, `m.tax`. Divide dollar
   quantities by `m.value` so the constraint is in weights and shares the verification tolerance.
3. If you need a quantity the model lacks, add it to `Model` and compute it once in `build`.
4. Add the class to the `Constraint` union.

## How to add a data source

1. In `src/optim/sources.py`, add the name to `SourceName`.
2. If the source is a database with a DB-API driver, wrap a connection factory in `Sql`. Otherwise, write a class with
   `fetch(self, query, params) -> Columns`.
3. Connect it in `open_sources` with a timeout, and close it in the `finally` block.
4. Add the name to the `loader()` helper in `tests/builders.py`.

## How to add an entry point

1. In `src/optim/jobs.py`, write a function that loads with `load_market` and `load_portfolios`, builds `Task`s,
   calls `solve_all`, and saves a snapshot with `save_snapshot` before acting on the results.
2. Add a subcommand for it in `_parser()` in `src/optim/__main__.py`, and a `case` in `main`.
3. Add one smoke test to `tests/test_jobs.py`.
