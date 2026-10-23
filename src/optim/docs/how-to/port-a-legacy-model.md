# How to port a legacy model

Bring an existing optimizer onto `optim` as a model, best effort: recover the business requirements its code encodes,
decide for each behaviour whether to port it, change it or drop it, and record every decision in a porting log. The old
system is a list of requirements, not the answer to match: once the port runs, the log shows what to revisit from
first principles.

Prerequisites:

- A model repo (`optim new repo`), or an existing one to add the model to.
- Read access to the old code, its SQL and its configuration, and someone who can say what each behaviour is for.
- One business day of the old system's inputs, and its trades for a few portfolios, for the
  [sanity check](#how-to-sanity-check-the-port).

To port a model:

1. Start the model from the closest example: `uv run optim new model NAME --from cvxpy` for a cvxpy problem, or
   `--from plain` for plain Python. It writes the model's package, `src/PACKAGE/NAME/`, where `PACKAGE` is the repo's
   package, with its `rules.py`, `datasets.py` and `README.md`; `params/NAME/default.json`; `tests/NAME/` with a fixture
   and behaviour tests; and the model's entry point in `pyproject.toml`. The example is a starting point, not a
   requirement: as the port lands, delete the example's rules, datasets and tests that the old system does not have.
2. Write the porting log, `PORTING.md` at the repo's root, one row per behaviour, before writing any code. Use the
   [porting log template](#porting-log-template), and search
   [where old code hides behaviour](#where-old-code-hides-behaviour).
   Add a row for each of `optim`'s [known limits](../explanation.md#known-limits) the old system contradicts, such as
   how it values a position or what it counts as cash.
3. Decide each row, and write down why:
   - **port**: a requirement, such as a client's restriction or a product's limit. Keep what it does.
   - **change**: keep the intent, build it differently. A fix applied to trades after the solve becomes a constraint,
     so the solve knows about it.
   - **drop**: [optim already does it](#what-optim-already-does), or it was a workaround for something `optim` does
     differently.
   - **ask**: nobody can say what it is for. Get an answer before step 4, or drop it and say so in the log.
4. Port the data: one dataset per query ([how to add a dataset](add-a-dataset.md)). Use the shared holdings and
   securities where the old system's data fits their row models; for columns they lack, add a dataset with the same
   key; for holdings read another way, such as at the start of the day, declare a dataset with the `Holding` row model
   and set it as `holdings` in the model's `Data`. Key securities by the old system's canonical id, as `security_id`.
   Data cleaning becomes a bound in a field's type, such as `Annotated[float, Field(gt=0)]`, or a dataset's `check`,
   so bad data fails the load instead of being patched silently. Give each dataset a file of a few made-up rows in the
   model's fixture.
5. Port each **port** and **change** row to its place in [where old code goes](#where-old-code-goes), and write its
   behaviour test as it lands. Put the test's name in the log.
6. Write the old system's settings as the model's default params, `params/NAME/default.json`. If the old system sets
   values per group of portfolios, such as per product, write one params file per group, and run each group's
   portfolios with its file.
7. [Sanity-check the port](#how-to-sanity-check-the-port) on the old system's data.

The port is done when every row of the log has a decision and, unless dropped, a place and a test; every portfolio in
the sanity check is accepted, or the log explains its reasons; the log records each difference the sanity check found;
and `uv run pytest` passes. Hand over the log with the code: its **change** and **drop** rows, and the differences the
sanity check recorded as intended, are where to start improving the model.

## Where old code hides behaviour

Search these as well as the optimizer itself:

- `WHERE`, `JOIN` and `CASE` clauses in SQL, which filter securities or portfolios before the optimizer sees them.
- `fillna`, defaults and fallbacks, which decide what missing data means.
- What happens when a feed or a restriction service is unavailable: a fallback that trades without restrictions is a
  behaviour to drop.
- How a position is valued and what counts as cash: clean or dirty prices, accrued interest, money-market sweeps,
  pending settlements, and which cash flows are applied.
- Literal numbers: thresholds, tolerances, caps.
- Branches on a portfolio, client, product or strategy id.
- Changes made to the trades after the solve: rounding, minimum sizes, dropped small trades.
- Retries and fallbacks when a solve fails, such as loosening a constraint or changing the solver.

## Porting log template

```markdown
| # | Behaviour | Old location | Category | Decision | Goes to | Why | Test |
|---|---|---|---|---|---|---|---|
| 1 | Skips bonds rated below A- | `filters.py:88` | excludes securities from buying | port | `rules.min_rating` | client requirement | `test_the_rating_floor_narrows_what_may_be_bought` |
| 2 | Drops buys under $10k after the solve | `post.py:31` | changes trades after the solve | change | constraint in `decide`, and `check` | the solve should know | `test_small_buys_are_not_decided` |
| 3 | Retries with turnover loosened when infeasible | `run.py:240` | retries a failed solve | drop | none | hides infeasibility | |
| 4 | Skips the restricted list when its service times out | `restrictions.py:57` | restricts trading whatever the params | change | `controls` dataset | a failed read must fail the run | `test_controls_apply_and_a_control_naming_no_security_applies_to_every_one` |
| 5 | Sends orders to the OMS | `orders.py` | sends orders | drop | none | optim saves orders; they are released elsewhere | |
```

`Category` is the first column of a row in [where old code goes](#where-old-code-goes).

## Where old code goes

Paths are in the model's directory, `src/PACKAGE/NAME/`, unless they say otherwise.

| Old code that | Goes to |
|---|---|
| Queries a database, file or API | A `Dataset` and its SQL, in `datasets.py` and `sql/` |
| Cleans or checks data | A bound in a field's type, or the dataset's `check` |
| Joins data per security, or computes what every portfolio shares | `prepare` |
| Values a position or counts cash | `prepare` and `prepare_portfolio`, from the securities' `price`, `accrued` and `price_per` ([the columns](../reference.md#shared-datasets)) |
| Restricts trading whatever the params: a client's restrictions, wash-sale blocks, mandatory sales | A per-portfolio dataset, applied first in `prepare_portfolio` |
| Excludes securities from buying, or stops them being sold, for some settings | A rule in `rules.py`, driven by a param |
| Adjusts returns or scores | A rule in `rules.py`, or a step of `prepare` |
| Sets a hard limit | A constraint in `decide`, and a reason in `check` where rounding could break it |
| Applies a penalty or a preference | A term of the objective in `decide` |
| Rounds trades, or drops small ones, after the solve | Nothing for rounding: `optim` rounds to each increment. A minimum size is a constraint in `decide` and a reason in `check` |
| Retries a failed solve | Nothing: the portfolio is rejected, `not solved`. A limit that may bend is a penalty instead |
| Checks a result before it is released | `check`, unless [optim already does it](#what-optim-already-does) |
| Sets solver options, or uses a licensed solver | `Params.solve`, an `optim.cvxpy.SolveSettings` |
| Sets values for a group of portfolios | Fields of `Params`, in one params file per group |
| Sets values that differ for every portfolio | A per-portfolio dataset, read in `prepare_portfolio` |
| Lets portfolios compete for a bond's supply | `reserve` and `order` ([how to decide portfolios one at a time](decide-portfolios-in-turn.md)) |
| Sends orders | Nothing: `optim` writes accepted orders to the orders table in production |
| Runs on a schedule | An Airflow task that runs the repo's image: `optim run NAME --params FILE --as-of DATE --portfolios ...` |

A rule reads only what `prepare_portfolio` gives it. If it needs data the model lacks, add a dataset first.

## What optim already does

Drop an old behaviour that does one of these, unless it does it differently on purpose:

- Fails the load on bad data: a value its field refuses, a duplicate key, or more rows than a source's `max_rows`.
- Reads a batch of portfolios' rows from one source in one read-only transaction.
- Rounds each trade to its security's increment, and sells an odd lot whole.
- Rejects a decision that was not solved, trades an unknown security, sells more than it holds, or overdraws cash.
- Saves every run's params, inputs and decisions, so any decision can be explained and replayed.

## How to sanity-check the port

Run the old system's inputs through the port and compare outcomes per portfolio. A large difference points at a
behaviour the port missed, or changed on purpose; it is not an error in itself. Exact trades rarely match: two correct
optimizers can trade differently on the same inputs, because an integer problem often has several best answers.

To sanity-check the port:

1. Export one business day of the old system's inputs to `old/` at the repo's root, one CSV or Parquet file per
   dataset of the port, named by the dataset's `name` (`old/holdings.csv` for `HOLDINGS`), with exactly the fields of
   its row model, which `uv run optim describe NAME` lists under `datasets`. Rename columns as the dataset's SQL aliases
   them, such as `account_id` to `portfolio_id`. Git ignores `old/`, which holds client data: `git check-ignore
   old/holdings.csv` prints the path when it is ignored.
2. Run the port on the export, for the export's date:

   ```sh
   uv run --env-file .env optim run NAME --params params/NAME/default.json --data old --as-of AS_OF_DATE --label purpose=porting
   ```

   Replace `AS_OF_DATE` with the export's business date, as `YYYY-MM-DD`. A dataset without a file in `old/` is read
   from its source, so export every one. Every dataset loads through its row model, so data the old system accepted
   but the port refuses fails here, naming the column and the row. The run prints its id and a row per portfolio.
3. Compare each portfolio's outcomes in `snapshots/RUN_ID/decisions.parquet` (`turnover`, `cash` and `trades`, among
   others) and `metrics.parquet` (the model's own) with the old system's, where it reports them. A rejected portfolio's
   reasons are in `reasons.parquet`. The [snapshot reference](../reference.md#snapshot-files) lists every column.
4. Optional: for a portfolio with a large gap, diff its trades with the old system's, by security. Export them as
   `old/trades.csv`, with `portfolio_id`, `security_id`, `side` (`buy` or `sell`) and `quantity` in the holdings'
   units; `--data` reads only files named after the model's datasets.

   ```python
   import pandas as pd

   key = ["portfolio_id", "security_id", "side"]
   new = pd.read_parquet("snapshots/RUN_ID/trades.parquet").groupby(key, as_index=False)["quantity"].sum()
   old = pd.read_csv("old/trades.csv").groupby(key, as_index=False)["quantity"].sum()  # the old system's trades
   diff = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True, validate="one_to_one")
   print(diff[diff["quantity_old"] != diff["quantity_new"]])
   ```

5. Record each difference in the log: a missed behaviour to fix, a different input, or an intended change.
6. After fixing a behaviour, replay the saved run with your code, `uv run --env-file .env optim replay RUN_ID`, and
   compare again. After changing a dataset's row model, run step 2 again, since a snapshot saved under the old model
   no longer loads.
