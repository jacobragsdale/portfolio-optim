# Porting another optimizer

Bring an existing optimizer's behaviour into this one: its data, rules, limits and preferences. Old code mixes these
together; here each has one place. Port in three passes: an inventory of what the old code does, the port itself, and
a comparison of results on the same day's data.

## How to port an optimizer

Prerequisites:

- Read access to the old code, its SQL and its configuration.
- One business day of the old system's inputs (bonds, offerings, signals, benchmarks, portfolios, lots, loss sales)
  and its trades for two or three portfolios. Without these, the port cannot be checked.

To port an optimizer:

1. Write the inventory: one row per behaviour, before writing any code. Use the
   [inventory template](#inventory-template).
2. Mark each row port, drop or ask, using [where old code goes](#where-old-code-goes) and
   [what is not ported](#what-is-not-ported). Get every ask answered by whoever owns the old system before step 3.
3. Port the data. For each input, alias the old columns to the field names of a row model in `src/optim/datasets/`, or
   add a dataset ([how to add a dataset](extending.md#how-to-add-a-dataset)). Data cleaning becomes a field
   annotation or a dataset `check`, so bad data fails the load instead of being patched silently.
4. Port each behaviour to its place, following [extending.md](extending.md) for rules, constraints and objective
   terms. Reuse an existing class with new parameters before writing a new one: `group_limit` with `overrides` covers
   most concentration limits.
5. Write the old system's settings as a strategy file in `strategies/`, such as `strategies/legacy.json`.
   `uv run optim schema` prints every kind and its parameters.
6. Compare results on the old system's data, as in [how to compare with the old system](#how-to-compare-with-the-old-system).
   Every difference is a missed behaviour, a different input, or an intended change; record which in the inventory.

## Inventory template

```markdown
| # | Behaviour | Old location | Kind | Goes to | Decision | Notes |
|---|---|---|---|---|---|---|
| 1 | Skip bonds rated below A- | `filters.py:88` | eligibility | `min_rating` rule | port | |
| 2 | Cash floor 1% | `config.yaml: min_cash` | limit | `cash_range` constraint | port | |
| 3 | Sends orders to the OMS | `orders.py` | output | none | drop | not ported |
```

`Kind` is a row of [where old code goes](#where-old-code-goes). Old code hides behaviour outside its optimizer, so
search these too:

- `WHERE`, `JOIN` and `CASE` clauses in SQL, which filter bonds or portfolios before the optimizer sees them.
- `fillna`, defaults and fallbacks, which decide what missing data means.
- Literal numbers: thresholds, tolerances, caps.
- Branches on a portfolio, client, account or strategy id.
- Changes made to the trades after the solve: rounding, minimum sizes, dropped small trades.
- Retries and fallbacks when a solve fails, such as loosening a constraint.

## Where old code goes

| Old code that | Goes to | Where |
|---|---|---|
| Queries a database, file or API | A `Dataset` and its SQL | `datasets/`, `datasets/sql/` |
| Cleans or checks data | A field annotation or a dataset `check` | `datasets/` |
| Joins data per bond | A merge in `build_market` | `inputs.py` |
| Decides what missing data means, as a `fillna` or a default does | Prep, if every strategy needs it; otherwise a rule (see [Preparing the inputs](architecture.md#preparing-the-inputs)) | `inputs.py`, `rules/` |
| Excludes bonds from buying | A rule that calls `inputs.restrict`, such as `exclude_issuers` for a restricted list | `rules/` |
| Adjusts returns or scores | A rule that edits `expected_return` | `rules/` |
| Sets a hard limit | A constraint | `optimizer/constraints/` |
| Applies a penalty or a preference | An objective term | `optimizer/objective.py` |
| Rounds trades to tradable sizes | Nothing: an integer solve trades exact sizes | `optimizer/model.py` |
| Drops small trades after the solve | The bond's `min_piece` (par); a dollar minimum is a new constraint on `m.buy` and `m.buying` | `datasets/sql/securities.sql`, `optimizer/constraints/` |
| Sets values shared by a group of portfolios, such as a client's | Fields in one strategy file per group | `strategies/` |
| Sets values that differ for every portfolio | A column on `PortfolioDetails` | `datasets/portfolios.py` |
| Writes reports or database rows | An output, after `save_run` | `outputs/` |
| Runs on a schedule | An Airflow-started Job | `deploy/job.yaml` |

A rule reads only `SolverInputs`. If a rule needs data the inputs lack, add a dataset and a column first.

## What is not ported

- Order submission. The optimizer never sends orders.
- Municipal bonds. The only tax statuses are `taxable` and `state_exempt`.
- "Account" as a name in code. It stays only in SQL Server table and column names, aliased to `portfolio_id`.
- Trade rounding after the solve as the default. Integer solves are the default; an LP solve (`"integer": false`) is
  rounded by `optimizer/rounding.py`.
- Solve retries that loosen a constraint. A portfolio that cannot be solved is reported unsolved, with the
  constraints it breaks before any trade.

## How to compare with the old system

`optim replay` solves the portfolios in a snapshot with any strategy, and needs no database. To compare, write the old
system's inputs as a snapshot, replay it with the ported strategy, and diff the trades.

1. Save the old inputs as a snapshot with a script like this one. It runs them through the same loading and
   validation as a live run, so data the old system accepted but this one refuses fails here, naming the column and
   row. Prep's checks across datasets, such as a benchmark bond without analytics, run when the snapshot is replayed.

   ```python
   from collections.abc import Mapping
   from datetime import date
   from pathlib import Path

   import pandas as pd

   from optim.data.loader import Loader
   from optim.datasets.benchmarks import BENCHMARKS
   from optim.datasets.portfolios import LOSS_SALES, LOTS, PORTFOLIOS
   from optim.datasets.securities import SECURITIES, UNIVERSE
   from optim.datasets.signals import SIGNALS
   from optim.inputs import load_frames
   from optim.outputs.snapshots import input_frames, save_snapshot
   from optim.sources import Columns, Param

   AS_OF_DATE = date(2026, 10, 16)
   OLD = {  # each dataset's query -> the old system's data, renamed to that dataset's row fields
       SECURITIES.query: pd.read_parquet("old/securities.parquet"),
       UNIVERSE.query: pd.read_parquet("old/offerings.parquet"),
       SIGNALS.query: pd.read_parquet("old/signals.parquet"),
       BENCHMARKS.query: pd.read_parquet("old/benchmarks.parquet"),
       PORTFOLIOS.query: pd.read_parquet("old/accounts.parquet").rename(columns={"account_id": "portfolio_id"}),
       LOTS.query: pd.read_parquet("old/lots.parquet").rename(columns={"account_id": "portfolio_id"}),
       LOSS_SALES.query: pd.read_parquet("old/loss_sales.parquet").rename(columns={"account_id": "portfolio_id"}),
   }


   class Old:
       def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
           return OLD[query].to_dict("list")


   loader = Loader(dict.fromkeys(("oracle", "mssql", "signals"), Old()))
   portfolio_ids = OLD[PORTFOLIOS.query]["portfolio_id"].tolist()
   frames = load_frames(loader, portfolio_ids, AS_OF_DATE)
   save_snapshot(Path("snapshots"), "old-2026-10-16", meta={"as_of_date": AS_OF_DATE}, frames=input_frames(frames))
   ```

   The `old/` files stand for however the old data is exported. Each frame must have exactly its row model's fields,
   listed in [snapshot files](#snapshot-files).

2. Replay it with the ported strategy:

   ```sh
   uv run optim replay snapshots/old-2026-10-16 --strategy strategies/legacy.json --workers 4
   ```

   The run prints a summary row per portfolio and saves its snapshot as `snapshots/RUN_ID`, where `RUN_ID` is the id
   on its first line. An unsolved portfolio's status names the constraints it breaks before any trade.

3. Diff the trades with the old system's, by bond:

   ```python
   import pandas as pd

   key = ["portfolio_id", "cusip", "side"]
   new = pd.read_parquet("snapshots/RUN_ID/trades.parquet").groupby(key, as_index=False)["par"].sum()
   old = pd.read_parquet("old/trades.parquet")  # the old system's trades: portfolio_id, cusip, side, par
   diff = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
   print(diff[diff["par_old"] != diff["par_new"]])
   ```

Exact trades rarely match, since solvers and rounding differ. Compare `summary.parquet` too: turnover, tax, cost,
income and cash per portfolio should be close. A large gap in one of them points at the behaviour to look for: tax at
lot selection and wash sales, turnover at limits, income at signals and eligibility.

## Snapshot files

`optim replay` reads these files from a snapshot directory, and prepares the solves from them as a live run does.

| File | Columns |
|---|---|
| `run.json` | An object with `as_of_date`, the business date as `YYYY-MM-DD`. Other keys are ignored. |
| `inputs/<dataset>.parquet` | One file per dataset, named as its `Dataset` is, with exactly its row model's fields, as loaded: `securities` (`Security`), `universe` (`Offering`), `signals` (`Signal`), `benchmarks` (`BenchmarkWeight`), `portfolios` (`PortfolioDetails`), `lots` (`Lot`) and `loss_sales` (`LossSale`). The models are in `src/optim/datasets/`. |

A run's snapshot also holds its results. `trades.parquet` has `portfolio_id`, `cusip`, `lot_id` (sells only), `side`
(`buy` or `sell`) and `par`. `positions.parquet` has post-trade `cusip`, `par` and `weight` per portfolio.
`summary.parquet` has one row per portfolio, including `status`, `objective`, and `value`, `cash`, `turnover`, `tax`, `cost` and
`income` in dollars.

## Glossary

Old code may use other words for these.

| Term | Meaning |
|---|---|
| Portfolio | What is optimized: cash plus tax lots, with tax rates and a benchmark. SQL Server calls it an account. |
| Tax lot | One purchase of a bond: par, cost price and acquired date. The optimizer sells by lot. |
| Long-term | A lot held over a year. Its gain is taxed at `lt_gain_rate`, otherwise `st_gain_rate`. |
| Wash sale | Buying a bond within 30 days of selling it at a loss, which disallows the loss. |
| Benchmark, target | Weights by bond that a portfolio is measured against, or moves toward. |
| Weight | Market value as a fraction of the portfolio's pre-trade value, cash included. |
| Par | Face amount. Prices are per 100 par, and dirty. |
| Increment | Trades are whole multiples of it. |
| Minimum piece | The smallest trade allowed. |
| Minimum remaining | The smallest amount a partial sale may leave in a lot. |
| Offering | Par of a bond on offer today. A bond with no offering cannot be bought. |
| Signal | Expected annual pre-tax return per bond, from the signals API. |
| Strategy | The JSON file that says how to optimize: rules, objective, constraints, solve. |
| Rule | A pre-solve edit to what may be bought or to expected returns. |
| Snapshot | A run's saved request, inputs and results, under `OPTIM_SNAPSHOT_DIR/RUN_ID`. |
