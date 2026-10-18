# Porting another optimizer

Bring an existing optimizer into this engine, best effort: recover the business requirements its code encodes, decide
for each behaviour whether to port it, change it or drop it, and record every decision in a porting log. The old
system is a list of requirements, not the answer to match: once the port runs, the log shows what to revisit from first
principles.

## How to port an optimizer

Prerequisites:

- This repository, with `uv sync` run.
- Read access to the old code, its SQL and its configuration, and someone who can say what each behaviour is for.
- One business day of the old system's inputs, and its trades for a few portfolios, for the
  [sanity check](#how-to-sanity-check-the-port).

To port an optimizer:

1. List the places the port writes code: `grep -rn "TODO(port)" src tests`. Each marker says what goes there, and
   [what to keep and what to replace](#what-to-keep-and-what-to-replace) says which parts of the repository are the
   engine and which are placeholders.
2. Write the porting log, one row per behaviour, before writing any code, in `docs/porting-log.md`: it is handed over
   with the code. Use the [porting log template](#porting-log-template), and search
   [where old code hides behaviour](#where-old-code-hides-behaviour).
3. Decide each row, and write down why:
   - **port**: a requirement, such as a client's restriction or a tax rule. Keep what it does.
   - **change**: keep the intent, build it differently. A fix applied to trades after the solve becomes a constraint,
     so the solve knows about it.
   - **drop**: [the engine already does it](#what-the-engine-already-does), or it was a workaround for something the
     engine does differently.
   - **ask**: nobody can say what it is for. Get an answer before step 4, or drop it and say so in the log.
4. Port the data. Replace the placeholder datasets and their SQL in `src/optim/datasets/` with the old system's
   queries, one `Dataset` per query ([how to add a dataset](extending.md#how-to-add-a-dataset)). Data cleaning becomes
   a bound in a field's type, such as `Annotated[float, Field(gt=0)]`, or a dataset's `check`, a function that raises
   on rows that disagree with each other, so bad data fails the load instead of being patched silently. Then make
   `load_frames`, `build_market` and `build_portfolios` in `src/optim/inputs.py` follow the new datasets, and give each
   dataset its row in `DEFAULTS` in `tests/builders.py`: one valid row per dataset, which every test frame starts from.
5. Port each **port** and **change** row to its place in [where old code goes](#where-old-code-goes), following
   [extending.md](extending.md). The example kinds are generic, so reuse one with new parameters before writing a new
   one: `uv run optim schema` lists every rule, term and constraint kind with its parameters, and `group_limit` with
   `overrides` (caps per group, such as per sector) covers most concentration limits. Delete the example classes that
   the port does not use, from their modules, their unions and `strategies/`, with their tests.
6. Write each behaviour's test as it lands. Build its frames from a few rows with `frame` and `market` in
   `tests/builders.py`, which fill each row's missing fields from `DEFAULTS`, as `tests/test_solve.py` does. For a
   rule, assert on the `SolverInputs` it returns; for a constraint or term, solve a small portfolio with and without it
   and assert on the trades. Put the test's name in the log.
7. Write the old system's settings as a strategy file in `strategies/`, such as `strategies/legacy.json`. If the old
   system sets values per group of portfolios, such as per client, write one strategy file per group.
8. [Sanity-check the port](#how-to-sanity-check-the-port) on the old system's data.

The port is done when:

- every row of the log has a decision and, unless dropped, a place and a test;
- no `TODO(port)` remains, or the log says why one stays;
- every portfolio in the sanity check solves, or the log explains its status, and the log records each difference the
  sanity check found;
- `uv run pre-commit run --all-files` and `uv run pytest` pass.

Hand over the log with the code and the strategy files: its **change** and **drop** rows, and the differences the
sanity check recorded as intended, are where to start improving the strategy.

## What to keep and what to replace

| Part | Paths | In a port |
|---|---|---|
| Engine | Under `src/optim/`: `data/`, `sources/`, `context.py`, `settings.py`, `engine.py`, `jobs/`, `outputs/`, `cli.py`, `logs.py`, `strategy.py`, `schema.py`, `optimizer/solve.py`, `optimizer/rounding.py`, `optimizer/results.py` | Keep. Add sources, jobs and outputs as [extending.md](extending.md) shows. |
| Placeholders | `src/optim/datasets/`, `strategies/`, `tests/builders.py` | Replace with the old system's data and settings. Their tables, columns and values are made up. |
| Business logic | Under `src/optim/`: prep in `inputs.py`; the rule, term and constraint classes in `rules/`, `optimizer/objective.py` and `optimizer/constraints/`, and the unions that list them; `optimizer/model.py` | Port from the old optimizer. What is here is one small example of each, not a requirement: keep an example only where the old optimizer does the same. |

`optimizer/model.py` holds the variables and expressions every constraint and term is written against, as `m`: buys
per bond (`m.buy`, with 0/1 flags `m.buying` in an integer solve), sales per tax lot (`m.sell`, `m.selling`),
weights, cash, tax and cost. Its tax is a placeholder too: each lot's gain from today's price, at the portfolio's
long- or short-term rate. Change the model where the old optimizer's formulation differs, such as in how it taxes gains
or sizes trades.

## Where old code hides behaviour

Old code hides behaviour outside its optimizer. Search these as well as the optimizer itself:

- `WHERE`, `JOIN` and `CASE` clauses in SQL, which filter bonds or portfolios before the optimizer sees them.
- `fillna`, defaults and fallbacks, which decide what missing data means.
- Literal numbers: thresholds, tolerances, caps.
- Branches on a portfolio, client, account or strategy id.
- Changes made to the trades after the solve: rounding, minimum sizes, dropped small trades.
- Retries and fallbacks when a solve fails, such as loosening a constraint.

## Porting log template

```markdown
| # | Behaviour | Old location | Category | Decision | Goes to | Why | Test |
|---|---|---|---|---|---|---|---|
| 1 | Skips bonds rated below A- | `filters.py:88` | excludes bonds from buying, for some strategies | port | `min_rating` rule | client requirement | `test_min_rating` |
| 2 | Drops buys under $10k after the solve | `post.py:31` | rounds trades or drops small trades after the solve | change | constraint on `m.buy` and `m.buying` | the solve should know | `test_min_buy` |
| 3 | Retries with turnover loosened when infeasible | `run.py:240` | retries a failed solve | drop | none | hides infeasibility | |
| 4 | Sends orders to the OMS | `orders.py` | sends orders | drop | none | out of scope | |
```

`Category` is the first column of a row in [where old code goes](#where-old-code-goes).

## Where old code goes

Paths are under `src/optim/` unless they start with `strategies/` or `deploy/`.

| Old code that | Goes to | Where |
|---|---|---|
| Queries a database, file or API | A `Dataset` and its SQL | `datasets/`, `datasets/sql/` |
| Cleans or checks data | A constraint in a field's type, or a dataset `check` | `datasets/` |
| Joins data per bond | A merge in `build_market` | `inputs.py` |
| Decides what missing data means, as a `fillna` or a default does | Prep, if every strategy needs it; otherwise a rule (see [preparing the inputs](architecture.md#preparing-the-inputs)) | `inputs.py`, `rules/` |
| Changes what each portfolio may buy or expects, whatever the strategy, such as after-tax returns or blocks on wash-sale buys | Prep in `Market.inputs_for`: a strategy that left it out would solve wrongly (see [preparing the inputs](architecture.md#preparing-the-inputs)) | `inputs.py` |
| Excludes bonds from buying, for some strategies | A rule that calls `inputs.restrict_buys`, such as `exclude_issuers` for a restricted list | `rules/` |
| Stops bonds or lots being sold, for some strategies, as a client's do-not-sell list does | A rule that calls `inputs.restrict_sales`; a list that differs by portfolio is a per-portfolio dataset the rule reads from `inputs.portfolio` | `rules/`, `datasets/` |
| Adjusts returns or scores for some strategies | A rule that edits `expected_return` | `rules/` |
| Sets a hard limit | A constraint | `optimizer/constraints/` |
| Applies a penalty or a preference | An objective term | `optimizer/objective.py` |
| Computes gains, taxes, value or cash inside the optimization | An expression in the model | `optimizer/model.py` |
| Rounds trades or drops small trades after the solve | The integer solve's trade sizes, from each bond's `min_piece`, `increment` and `min_remaining`; a dollar minimum is a constraint on `m.buy` and `m.buying`, or on `m.sell` and `m.selling` | `optimizer/model.py`, `optimizer/constraints/` |
| Retries a failed solve, as by loosening a constraint | Nothing: the portfolio is reported unsolved, with what it breaks before any trade. A limit that may bend is an objective penalty instead. | `optimizer/objective.py` |
| Sets values shared by a group of portfolios, such as a client's | Fields in one strategy file per group | `strategies/` |
| Sets values that differ for every portfolio | A column on `PortfolioDetails`, read as `inputs.portfolio.details.<column>` | `datasets/portfolios.py` |
| Is another entry point, such as model portfolios or transition analysis | A job ([how to add a job](extending.md#how-to-add-a-job)) | `jobs/` |
| Writes reports or database rows | An output, called in `run` in `jobs/__init__.py` once the snapshot is saved | `outputs/` |
| Sends orders | Nothing: the engine never sends orders | |
| Runs on a schedule | An AKS Job that Airflow starts, from the Job spec | `deploy/job.yaml` |

A rule reads only `SolverInputs`. If a rule needs data the inputs lack, add a dataset and a column first.

## What the engine already does

Drop an old behaviour that does one of these, unless it does it differently on purpose:

- Fails the load on bad data: a value its field refuses, or a duplicate key.
- Re-checks every constraint at the exact trades, and saves any breach.
- Reports a portfolio it cannot solve as unsolved, with the constraints it breaks before any trade.
- Saves every run's request, inputs and results, so any result can be explained and replayed.

The placeholder prep and model also do these. Drop the old code for them only if it works the same way:

- Fails the run on a market dataset with no rows, or a benchmark bond without analytics.
- Trades exact sizes in an integer solve: whole increments, at least the minimum piece, leaving at least the minimum
  remaining in a lot. An LP solve (`"integer": false`) is rounded to such sizes afterwards.
- Chooses which tax lots to sell, from each lot's gain and holding period.

## How to sanity-check the port

Run the old system's inputs through the port and compare outcomes per portfolio. A large difference points at a
behaviour the port missed, or changed on purpose; it is not an error in itself. Exact trades rarely match: two correct
optimizers can trade differently on the same inputs, because an integer problem often has several best answers.

To sanity-check the port:

1. Export one business day of the old system's inputs to `old/` at the repository root, one Parquet file per dataset
   of the port, named by the name its `Dataset` declares (`old/securities.parquet` for
   `Dataset("securities", ...)`), with exactly the fields of its row model. Rename columns as the dataset's SQL aliases
   them, such as `account_id` to `portfolio_id`. Git ignores `old/`, which holds client data.
2. Save this script as `sanity.py` at the repository root, where git ignores it too. Set `AS_OF_DATE` to the export's
   business date, and run it with `uv run python sanity.py`. It loads every file as a live run loads its datasets, so
   data the old system accepted but the port refuses fails here, naming the column and row. Then it solves every
   portfolio with `strategies/legacy.json` and saves the run as `snapshots/old`, which must not exist yet.

   ```python
   from collections.abc import Mapping
   from datetime import date
   from pathlib import Path

   import pandas as pd

   from optim.context import Context
   from optim.data.loader import Loader
   from optim.datasets import all_datasets
   from optim.jobs import run
   from optim.jobs.optimize import OptimizeRequest
   from optim.optimizer.results import result_frames
   from optim.sources import Columns, Param
   from optim.strategy import Strategy

   AS_OF_DATE = date(2026, 10, 16)
   RUN_ID = "old"
   OLD = {dataset.query: pd.read_parquet(f"old/{dataset.name}.parquet") for dataset in all_datasets()}


   class Old:
       """A source that answers each dataset's query with its file, and a batch of portfolios with their rows alone."""

       def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
           frame = OLD[query]
           if isinstance(batch := params.get("portfolios"), tuple):  # the batch `_load_portfolios` binds
               frame = frame[frame["portfolio_id"].isin(batch)]
           return frame.to_dict("list")


   def main() -> None:
       loader = Loader({dataset.source: Old() for dataset in all_datasets()})
       context = Context(loader, Path("snapshots"), workers=4, new_run_id=lambda: RUN_ID)
       strategy = Strategy.model_validate_json(Path("strategies/legacy.json").read_bytes())
       portfolio_ids = pd.read_parquet("old/portfolios.parquet")["portfolio_id"].tolist()
       ran = run(context, OptimizeRequest(strategy=strategy, portfolios=portfolio_ids, as_of_date=AS_OF_DATE), labels={"purpose": "porting"})
       print(result_frames(ran.results)["summary"].to_string(index=False))


   if __name__ == "__main__":  # solver processes import this module, and must not run it
       main()
   ```

   If the old system has a strategy per group of portfolios, run the script once per group, with the group's strategy
   file, its portfolio ids and its own `RUN_ID`. The script runs the `optimize` job; check another job the same way,
   with its request in place of `OptimizeRequest`.

3. Compare each portfolio's outcomes in `snapshots/old/summary.parquet` with the old system's, where it
   reports them: `turnover`, `tax`, `cost`, `income` and `cash`, in dollars, and the number of trades. `Summary` and
   `Trade` in `src/optim/optimizer/results.py` define each column. An unsolved portfolio's `status` names the
   constraints it breaks before any trade. A gap you cannot explain from the inputs points at the behaviour to look
   for: tax at lot selection, turnover at limits, income at signals and eligibility.
4. Optional: for a portfolio with a large gap, diff its trades with the old system's, by bond:

   ```python
   import pandas as pd

   key = ["portfolio_id", "cusip", "side"]
   new = pd.read_parquet("snapshots/old/trades.parquet").astype({"side": "str"}).groupby(key, as_index=False)["par"].sum()
   # the old system's trades, per bond or per lot: portfolio_id, cusip, side ("buy" or "sell"), par
   old = pd.read_parquet("old/trades.parquet").groupby(key, as_index=False)["par"].sum()
   diff = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
   print(diff[diff["par_old"] != diff["par_new"]])
   ```

5. Record each difference in the porting log: a missed behaviour to fix, a different input, or an intended change.
6. After fixing a behaviour, replay the saved run with your code:

   ```sh
   uv run optim replay snapshots/old --workers 4
   ```

   After editing `strategies/legacy.json`, pass it with `--strategy strategies/legacy.json`. A replay prints
   `run RUN_ID`, then a summary row per portfolio, and saves its run as `snapshots/RUN_ID`: read its
   `summary.parquet` and `trades.parquet` in steps 3 and 4. After changing a dataset's row model, delete
   `snapshots/old` and run `sanity.py` again, since a snapshot saved under the old model no longer loads.

## Glossary

The engine's words, and the placeholder data's. Old code may use other words for these.

| Term | Meaning |
|---|---|
| Portfolio | What is optimized: cash plus tax lots, with capital gains tax rates and a benchmark. The code says portfolio; "account" stays only in SQL Server's table and column names, aliased to `portfolio_id`. |
| Tax lot | One purchase of a bond: par, cost price and acquired date. The optimizer sells by lot. |
| Long-term | A lot held over a year. Its gain is taxed at `lt_gain_rate`, otherwise `st_gain_rate`. |
| Benchmark, target | Weights by bond that a portfolio is measured against, or moves toward. |
| Weight | Market value as a fraction of the portfolio's pre-trade value, cash included. |
| Par | Face amount. Prices are per 100 par. |
| Increment | Trades are whole multiples of it. |
| Minimum piece | The smallest trade allowed. |
| Minimum remaining | The smallest amount a partial sale may leave in a lot. |
| Offering | Par of a bond on offer today. A bond with no offering cannot be bought. |
| Signal | Expected annual return per bond, from the signals API. |
| Prep | What every strategy needs from the datasets, before the rules: joins, what missing data means, checks across datasets. `build_market`, `build_portfolios` and `Market.inputs_for` in `inputs.py`. |
| Strategy | The JSON file that says how to optimize: rules, objective, constraints, solve. |
| Rule | A pre-solve edit to what may be bought or sold, or to expected returns. |
| Kind | The `kind` that names a rule, term or constraint in a strategy file, such as `min_rating`. |
| Snapshot | A run's saved request, inputs and results: see [snapshot files](snapshots.md). |
