# Extending the optimizer

Each kind of extension is one new module, or one new class in an existing module, plus a line where it is collected.
None needs a registry or factory. Run `uv run pre-commit run --all-files` and `uv run pytest` after each change.

## How to add a dataset

To load a new table, for example issuer credit scores from Oracle:

1. Pick the module in `src/optim/datasets/` for its subject, or add one, such as `issuers.py`.
2. Write its row model there. Field names are the column names the query returns. Put every field rule in the
   annotation, such as `score: Annotated[float, Field(ge=0, le=100)]`, because rows are validated column by column.
3. Add `src/optim/datasets/sql/issuer_scores.sql`. Alias each column to its field name. Bind parameters as `:name` for
   Oracle or `%(name)s` for SQL Server.
4. Declare the dataset beside its row model:
   `ISSUER_SCORES = Dataset("issuer_scores", IssuerScore, source="oracle", query="datasets/sql/issuer_scores.sql", key=("issuer",), ttl=timedelta(hours=1))`.
   Leave out `ttl` if the data must always be read live.
5. Load it in `src/optim/inputs.py`. Per-security data goes in `load_market`: add it to the `loader.load_all(...)`
   call and merge `frames["issuer_scores"]` into `securities` with `validate="many_to_one"` or `"one_to_one"`.
   Per-account data goes in `load_portfolios` through `loader.load_batched`, and its query unpacks the ids with
   `OPENJSON` or `JSON_TABLE`.
6. Add the dataset's records to `tables()` in `tests/builders.py`, keyed by its `query`, so every test still loads.

A rule or constraint can now read the new column from `inputs.securities` or `model.securities`.

## How to add a rule

1. Pick the module in `src/optim/rules/` for the rule's family (`eligibility`, `tax`), or add one.
2. Write a `Strict` class with a `kind: Literal["your_rule"] = "your_rule"` field, its parameters as fields, and
   `apply(self, inputs: Inputs) -> Inputs`.
3. Narrow buying with `inputs.restrict(allowed)`, or return `replace(inputs, securities=inputs.securities.assign(...))`
   to adjust a column. Never drop rows.
4. Add the class to the `Rule` union in `src/optim/rules/__init__.py`.
5. Add a table-driven test to `tests/test_rules.py` covering the rule's boundary.

Strategy files can use the rule as soon as it is in the union.

## How to add a constraint

1. Pick the module in `src/optim/optimizer/constraints/` for the constraint's family (`limits`, `risk`, `trading`,
   `tax`), or add one.
2. Write a `Strict` class with a `kind` field, its parameters, and `build(self, m: Model) -> list[cp.Constraint]`.
3. Write the constraint against the model's expressions: `m.weights`, `m.cash`, `m.turnover`, `m.tax`. Divide dollar
   quantities by `m.value` so the constraint is in weights and shares the verification tolerance.
4. If you need a quantity the model lacks, add it to `Model` in `src/optim/optimizer/model.py` and compute it once in
   `build_model`.
5. Add the class to the `Constraint` union in `src/optim/optimizer/constraints/__init__.py`.

## How to add a data source

1. Add a module to `src/optim/sources/`, such as `pricing.py`, with a settings model (a `Strict` subclass) and a
   `connect` function. A database with a DB-API driver returns a `Sql` over a connection factory; anything else
   returns a class with `fetch(self, query, params) -> Columns`. Put a timeout on every call.
2. Add the source's name to `SourceName` in `src/optim/sources/__init__.py`.
3. Add its settings to `Settings` in `src/optim/settings.py`, and its variables to `.env.example`.
4. Open it in `open_context` in `src/optim/context.py`. If it holds a pool or client, enter it on the `ExitStack` so it
   closes.
5. Add the name to the `loader()` helper in `tests/builders.py`.

If the source is another JSON API, it needs only steps 2 to 5: reuse `ApiSettings`, `rest.client` and `Rest`.

## How to add an entry point

1. Add a module to `src/optim/jobs/` with a request model (a `Strict` subclass) and a function taking
   `(ctx: Context, request: YourRequest)`. Load with `load_market` and `load_portfolios`, build `Task`s, call
   `solve_all`, and save the run with `save_run` before acting on the results.
2. Add a subcommand to `_parser()` in `src/optim/cli.py` that builds the request, and a `case` for it in `main`.
3. Add one smoke test to `tests/test_jobs.py`, using the `context()` builder.
