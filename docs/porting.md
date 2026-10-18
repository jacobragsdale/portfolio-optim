# Porting another optimizer

Bring an existing optimizer into this engine, best effort: recover the business requirements its code encodes, decide
for each behaviour whether to port it, change it or drop it, and record every decision in a porting log. The old
system is a list of requirements, not the answer to match: once the port runs, the log shows what to revisit from first
principles.

## How to port an optimizer

Prerequisites:

- Read access to the old code, its SQL and its configuration, and someone who can say what each rule was for.
- One business day of the old system's inputs (bonds, offerings, signals, benchmarks, portfolios, lots, loss sales),
  and its trades for a few portfolios, for the [sanity check](#how-to-sanity-check-the-port).

To port an optimizer:

1. Read [what to keep, replace and adapt](#what-to-keep-replace-and-adapt): which parts of this repository are the
   engine, and which are examples to replace with the old optimizer's data and logic.
2. Write the porting log, one row per behaviour, before writing any code. Use the
   [porting log template](#porting-log-template), and search the places where old code hides behaviour.
3. Decide each row, and write down why:
   - **port**: a requirement, such as a client's restriction or a tax rule. Keep what it does.
   - **change**: keep the intent, build it differently. A fix applied to trades after the solve becomes a constraint,
     so the solve knows about it.
   - **drop**: the engine already does it, or it was a workaround: see [what is not ported](#what-is-not-ported).
   - **ask**: nobody can say what it is for. Get an answer before step 5, or drop it and say so in the log.
4. Port the data. Replace the example datasets and their SQL in `src/optim/datasets/` with the old system's sources:
   alias the old columns to a row model's field names, or add a dataset
   ([how to add a dataset](extending.md#how-to-add-a-dataset)). Data cleaning becomes a field annotation or a dataset
   `check`, so bad data fails the load instead of being patched silently.
5. Port each **port** and **change** row to its place in [where old code goes](#where-old-code-goes), following
   [extending.md](extending.md). Reuse an existing class with new parameters before writing a new one: `group_limit`
   with `overrides` covers most concentration limits.
6. Write each behaviour's test as it lands: build a few rows per dataset, as `tests/test_inputs.py` does, and assert on
   what the rule, constraint or term returns. Put the test's name in the log.
7. Write the old system's settings as a strategy file in `strategies/`, such as `strategies/legacy.json`.
   `uv run optim schema` prints every kind and its parameters.
8. [Sanity-check the port](#how-to-sanity-check-the-port) on the old system's data. Record each large difference in
   the log as a missed behaviour (fix it), a different input, or an intended change.

The port is done when every row of the log has a decision and, unless dropped, a place and a test, and a replay of
the old day's data solves every portfolio or explains why not. The **change** and **drop** rows, and the intended
differences, are where to start improving the strategy.

## Porting log template

```markdown
| # | Behaviour | Old location | Kind | Decision | Goes to | Why | Test |
|---|---|---|---|---|---|---|---|
| 1 | Skips bonds rated below A- | `filters.py:88` | eligibility | port | `min_rating` rule | client requirement | `test_min_rating` |
| 2 | Drops buys under $10k after the solve | `post.py:31` | post-solve fix | change | constraint on `m.buy` and `m.buying` | the solve should know | `test_min_buy` |
| 3 | Retries with turnover loosened when infeasible | `run.py:240` | fallback | drop | none | hides infeasibility | |
| 4 | Sends orders to the OMS | `orders.py` | output | drop | none | not ported | |
```

`Kind` is a row of [where old code goes](#where-old-code-goes). Old code hides behaviour outside its optimizer, so
search these too:

- `WHERE`, `JOIN` and `CASE` clauses in SQL, which filter bonds or portfolios before the optimizer sees them.
- `fillna`, defaults and fallbacks, which decide what missing data means.
- Literal numbers: thresholds, tolerances, caps.
- Branches on a portfolio, client, account or strategy id.
- Changes made to the trades after the solve: rounding, minimum sizes, dropped small trades.
- Retries and fallbacks when a solve fails, such as loosening a constraint.

## What to keep, replace and adapt

| Part | Paths under `src/optim/` | In a port |
|---|---|---|
| Engine | `data/`, `sources/`, `context.py`, `settings.py`, `engine.py`, `jobs/`, `outputs/`, `cli.py`, `logs.py`, `strategy.py`, `optimizer/solve.py`, `optimizer/rounding.py`, `optimizer/results.py` | Keep. Add sources, jobs and outputs as [extending.md](extending.md) shows. |
| Examples | `datasets/`, `datasets/sql/`, and `strategies/` at the top level | Replace with the old system's data and settings, in the same pattern. |
| Domain | `inputs.py` (`build_market`: joins and house policy), `rules/`, `optimizer/objective.py`, `optimizer/constraints/`, `optimizer/model.py`, `schema.py` | Keep what the old optimizer also needs, change what differs, add what is missing. |

`optimizer/model.py` holds the variables every constraint and term is written against: buys per bond, sells per tax
lot, weights, cash, tax and cost. Change it only for a behaviour no constraint or term can express.

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
| Writes reports or database rows | An output, called in `run` once the snapshot is saved | `outputs/` |
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

## How to sanity-check the port

Run the old system's inputs through the port and compare outcomes per portfolio. A large difference points at a
behaviour the port missed, or changed on purpose; it is not an error in itself. Exact trades rarely match: two correct
optimizers can trade differently on the same inputs, because an integer problem often has several best answers.

To sanity-check the port:

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
   from optim.jobs.optimize import OptimizeRequest
   from optim.outputs.snapshots import save_run
   from optim.sources import Columns, Param
   from optim.strategy import Strategy

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
           frame = OLD[query]
           if isinstance(batch := params.get("portfolios"), tuple):  # a batch of portfolio ids
               frame = frame[frame["portfolio_id"].isin(batch)]
           return frame.to_dict("list")


   loader = Loader(dict.fromkeys(("oracle", "mssql", "signals"), Old()))
   portfolio_ids = OLD[PORTFOLIOS.query]["portfolio_id"].tolist()
   strategy = Strategy.model_validate_json(Path("strategies/legacy.json").read_bytes())
   request = OptimizeRequest(strategy=strategy, portfolios=portfolio_ids, as_of_date=AS_OF_DATE)
   frames = load_frames(loader, portfolio_ids, AS_OF_DATE)
   save_run(Path("snapshots"), "old-2026-10-16", job="optimize", as_of_date=AS_OF_DATE, request=request, frames=frames, results=[])
   ```

   The `old/` files stand for however the old data is exported. Each frame must have exactly its row model's fields,
   listed in [snapshot files](#snapshot-files).

2. Replay it with the ported strategy, which the snapshot's request holds:

   ```sh
   uv run optim replay snapshots/old-2026-10-16 --workers 4
   ```

   The run prints a summary row per portfolio and saves its snapshot as `snapshots/RUN_ID`, where `RUN_ID` is the id
   on its first line. An unsolved portfolio's status names the constraints it breaks before any trade.

3. Compare each portfolio's outcomes in `snapshots/RUN_ID/summary.parquet` with the old system's, where it reports
   them: turnover, tax, cost, income and cash, and the number of trades. A large gap points at the behaviour to look
   for: tax at lot selection and wash sales, turnover at limits, income at signals and eligibility.
4. Optional: for a portfolio with a large gap, diff its trades with the old system's, by bond:

   ```python
   import pandas as pd

   key = ["portfolio_id", "cusip", "side"]
   new = pd.read_parquet("snapshots/RUN_ID/trades.parquet").groupby(key, as_index=False)["par"].sum()
   old = pd.read_parquet("old/trades.parquet")  # the old system's trades: portfolio_id, cusip, side, par
   diff = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
   print(diff[diff["par_old"] != diff["par_new"]])
   ```

5. Record each difference in the porting log: a missed behaviour to fix, a different input, or an intended change.

## Snapshot files

`optim replay` reads these files from a snapshot directory, and prepares the solves from them as a live run does.

| File | Columns |
|---|---|
| `run.json` | An object with `run_id` and `request`, the job's request as JSON, with `job` naming the job. Replay reads only these two; the other keys are for people. |
| `inputs/<dataset>.parquet` | One file per dataset, named as its `Dataset` is, with exactly its row model's fields, as loaded: `securities` (`Security`), `universe` (`Offering`), `signals` (`Signal`), `benchmarks` (`BenchmarkWeight`), `portfolios` (`PortfolioDetails`), `lots` (`Lot`) and `loss_sales` (`LossSale`). The models are in `src/optim/datasets/`. |

A run's snapshot also holds its results, each with exactly the fields of its row model in
`src/optim/optimizer/results.py`, whatever the run produced:

| File | One row per | Row model |
|---|---|---|
| `trades.parquet` | trade: a buy of a bond, or a sale from one tax lot | `Trade` |
| `positions.parquet` | bond held after trading | `Position` |
| `summary.parquet` | solve, with its status and its dollar totals; null numbers when it did not solve | `Summary` |
| `violations.parquet` | constraint broken at the exact trades, with the excess in weights | `Violation` |

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
