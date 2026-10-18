# Porting another optimizer

Bring an existing optimizer into this engine, best effort: recover the business requirements its code encodes, decide
for each behaviour whether to port it, change it or drop it, and record every decision in a porting log. The old
system is a list of requirements, not the answer to match: once the port runs, the log shows what to revisit from first
principles.

## How to port an optimizer

Prerequisites:

- Read access to the old code, its SQL and its configuration, and someone who can say what each behaviour is for.
- One business day of the old system's inputs (bonds, offerings, signals, benchmarks, portfolios, tax lots, and the
  bonds each portfolio sold at a loss in the last 30 days), and its trades for a few portfolios, for the
  [sanity check](#how-to-sanity-check-the-port).
- The engine covers taxable bonds: a bond's tax status is `taxable` or `state_exempt`. Municipal bonds are out of scope.

To port an optimizer:

1. Read [what to keep, replace and adapt](#what-to-keep-replace-and-adapt): which parts of this repository are the
   engine, and which are examples to replace with the old optimizer's data and logic.
2. Write the porting log, one row per behaviour, before writing any code. Use the
   [porting log template](#porting-log-template), and search [where old code hides behaviour](#where-old-code-hides-behaviour).
3. Decide each row, and write down why:
   - **port**: a requirement, such as a client's restriction or a tax rule. Keep what it does.
   - **change**: keep the intent, build it differently. A fix applied to trades after the solve becomes a constraint,
     so the solve knows about it.
   - **drop**: [the engine already does it](#what-the-engine-already-does), or it was a workaround for something the
     engine does differently.
   - **ask**: nobody can say what it is for. Get an answer before step 4, or drop it and say so in the log.
4. Port the data. Replace the example datasets and their SQL in `src/optim/datasets/` with the old system's sources:
   alias the old columns to a row model's field names, or add a dataset
   ([how to add a dataset](extending.md#how-to-add-a-dataset)). Data cleaning becomes a constraint in the field's type,
   such as `Annotated[float, Field(gt=0)]`, or a dataset `check` across rows, so bad data fails the load instead of
   being patched silently.
5. Port each **port** and **change** row to its place in [where old code goes](#where-old-code-goes), following
   [extending.md](extending.md). Reuse an existing kind with new parameters before writing a new one:
   `uv run optim schema` lists every rule, term and constraint kind with its parameters, and `group_limit` with
   `overrides` (caps per group, such as per sector) covers most concentration limits. Delete the example rules,
   constraints and terms the port does not use.
6. Write each behaviour's test as it lands, from a few rows per dataset, as `tests/test_inputs.py` builds them. For a
   rule, assert on the `SolverInputs` it returns; for a constraint or term, solve a small portfolio with and without it
   and assert on the trades. Put the test's name in the log.
7. Write the old system's settings as a strategy file in `strategies/`, such as `strategies/legacy.json`. If the old
   system sets values per group of portfolios, such as per client, write one strategy file per group.
8. [Sanity-check the port](#how-to-sanity-check-the-port) on the old system's data.

The port is done when every row of the log has a decision and, unless dropped, a place and a test, and every portfolio
in the sanity check solves, or its unsolved status is explained in the log. Hand over the log with the code and the
strategy files: its **change** and **drop** rows, and the differences the sanity check recorded as intended, are where
to start improving the strategy.

## What to keep, replace and adapt

| Part | Paths under `src/optim/` | In a port |
|---|---|---|
| Engine | `data/`, `sources/`, `context.py`, `settings.py`, `engine.py`, `jobs/`, `outputs/`, `cli.py`, `logs.py`, `strategy.py`, `optimizer/solve.py`, `optimizer/rounding.py`, `optimizer/results.py` | Keep. Add sources, jobs and outputs as [extending.md](extending.md) shows. |
| Examples | `datasets/` and `datasets/sql/`, and `strategies/` at the top level | Replace with the old system's data and settings, in the same pattern. |
| Domain | `inputs.py`, `rules/`, `optimizer/objective.py`, `optimizer/constraints/`, `optimizer/model.py`, `schema.py` | Keep what the old optimizer also needs, change what differs, add what is missing. |

The example datasets are not a contract. `load_frames` loads them by name, and `build_market` and `build_portfolios`
prepare them ([preparing the inputs](architecture.md#preparing-the-inputs)), so a dataset added, renamed or removed
changes those functions with it, and the tests that build its rows. Replay reads whatever a snapshot holds.

`optimizer/model.py` holds the variables and expressions every constraint and term is written against, as `m`: buys
per bond (`m.buy`, with 0/1 flags `m.buying`), sales per tax lot (`m.sell`, `m.selling`), weights, cash, tax and cost.
Change it only for a behaviour no constraint or term can express. `schema.py` holds the field types datasets and
strategies share, such as `Cusip`, `Rating` and `Rate`.

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
| # | Behaviour | Old location | Kind | Decision | Goes to | Why | Test |
|---|---|---|---|---|---|---|---|
| 1 | Skips bonds rated below A- | `filters.py:88` | excludes bonds from buying | port | `min_rating` rule | client requirement | `test_min_rating` |
| 2 | Drops buys under $10k after the solve | `post.py:31` | drops small trades after the solve | change | constraint on `m.buy` and `m.buying` | the solve should know | `test_min_buy` |
| 3 | Retries with turnover loosened when infeasible | `run.py:240` | retries a failed solve | drop | none | hides infeasibility | |
| 4 | Sends orders to the OMS | `orders.py` | sends orders | drop | none | out of scope | |
```

`Kind` is the first column of a row in [where old code goes](#where-old-code-goes).

## Where old code goes

| Old code that | Goes to | Where |
|---|---|---|
| Queries a database, file or API | A `Dataset` and its SQL | `datasets/`, `datasets/sql/` |
| Cleans or checks data | A constraint in a field's type, or a dataset `check` | `datasets/` |
| Joins data per bond | A merge in `build_market` | `inputs.py` |
| Decides what missing data means, as a `fillna` or a default does | Prep, if every strategy needs it; otherwise a rule (see [preparing the inputs](architecture.md#preparing-the-inputs)) | `inputs.py`, `rules/` |
| Excludes bonds from buying | A rule that calls `inputs.restrict`, such as `exclude_issuers` for a restricted list | `rules/` |
| Adjusts returns or scores | A rule that edits `expected_return` | `rules/` |
| Sets a hard limit | A constraint | `optimizer/constraints/` |
| Applies a penalty or a preference | An objective term | `optimizer/objective.py` |
| Rounds trades to tradable sizes | Nothing: an integer solve trades exact sizes | `optimizer/model.py` |
| Drops small trades after the solve | The bond's `min_piece` (par); a dollar minimum is a new constraint on `m.buy` and `m.buying`, or on `m.sell` and `m.selling` for sales | `datasets/sql/securities.sql`, `optimizer/constraints/` |
| Retries a failed solve, as by loosening a constraint | Nothing: the portfolio is reported unsolved, with what it breaks before any trade. A limit that may bend is an objective penalty instead. | `optimizer/objective.py` |
| Sets values shared by a group of portfolios, such as a client's | Fields in one strategy file per group | `strategies/` |
| Sets values that differ for every portfolio | A column on `PortfolioDetails`, read as `inputs.portfolio.details.<column>`; give the all-cash model portfolio a value for it in `jobs/model_portfolio.py` | `datasets/portfolios.py` |
| Writes reports or database rows | An output, called in `run` in `jobs/__init__.py` once the snapshot is saved | `outputs/` |
| Sends orders | Nothing: the engine never sends orders | |
| Runs on a schedule | An Airflow-started Job | `deploy/job.yaml` |

A rule reads only `SolverInputs`. If a rule needs data the inputs lack, add a dataset and a column first.

## What the engine already does

Drop an old behaviour that does one of these, unless it does it differently on purpose:

- Trades exact sizes: whole increments, at least the minimum piece, leaving at least the minimum remaining in a lot.
  An LP solve (`"integer": false`) is rounded to such sizes afterwards.
- Chooses which tax lots to sell, from each lot's gain and holding period.
- Blocks buys that would make a wash sale, with the `no_wash_sales` rule.
- Re-checks every constraint at the exact trades, and saves any breach.
- Reports a portfolio it cannot solve as unsolved, with the constraints it breaks before any trade.
- Saves every run's request, inputs and results, so any result can be explained and replayed.

## How to sanity-check the port

Run the old system's inputs through the port and compare outcomes per portfolio. A large difference points at a
behaviour the port missed, or changed on purpose; it is not an error in itself. Exact trades rarely match: two correct
optimizers can trade differently on the same inputs, because an integer problem often has several best answers.

To sanity-check the port:

1. Save the old inputs as a snapshot with a script like this one. It loads them as a live run does, so data the old
   system accepted but this one refuses fails here, naming the column and row. Checks across datasets, such as a
   benchmark bond without analytics, run when the snapshot is replayed.

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

   The `old/` files stand for however the old data is exported, with exactly the fields of each dataset's row model in
   `src/optim/datasets/`. If the port has changed the datasets, list the port's. If the old system has a strategy per
   group of portfolios, save one snapshot per group, each with its strategy and its portfolios.

2. Replay it. The snapshot's request holds the ported strategy:

   ```sh
   uv run optim replay snapshots/old-2026-10-16 --workers 4
   ```

   The run prints a summary row per portfolio and saves its snapshot beside the one it replays, as
   `snapshots/RUN_ID`, where `RUN_ID` is the id on its first line. An unsolved portfolio's status names the
   constraints it breaks before any trade.

3. Compare each portfolio's outcomes in `snapshots/RUN_ID/summary.parquet` with the old system's, where it reports
   them: `turnover`, `tax`, `cost`, `income` and `cash`, in dollars, and the number of trades. A gap you cannot explain
   from the inputs points at the behaviour to look for: tax at lot selection and wash sales, turnover at limits, income
   at signals and eligibility. [Snapshot files](snapshots.md) defines every column.
4. Optional: for a portfolio with a large gap, diff its trades with the old system's, by bond:

   ```python
   import pandas as pd

   key = ["portfolio_id", "cusip", "side"]
   new = pd.read_parquet("snapshots/RUN_ID/trades.parquet").astype({"side": "str"}).groupby(key, as_index=False)["par"].sum()
   old = pd.read_parquet("old/trades.parquet")  # the old system's trades: portfolio_id, cusip, side ("buy" or "sell"), par
   diff = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
   print(diff[diff["par_old"] != diff["par_new"]])
   ```

5. Record each difference in the porting log: a missed behaviour to fix, a different input, or an intended change.
6. After fixing a behaviour, replay the same snapshot again. A change to the code needs nothing else. After editing
   `strategies/legacy.json`, pass it: `uv run optim replay snapshots/old-2026-10-16 --strategy strategies/legacy.json`.
   After changing a dataset's row model, save the snapshot again with the script.

## Glossary

Old code may use other words for these.

| Term | Meaning |
|---|---|
| Portfolio | What is optimized: cash plus tax lots, with tax rates and a benchmark. The code says portfolio; "account" stays only in SQL Server's table and column names, aliased to `portfolio_id`. |
| Tax lot | One purchase of a bond: par, cost price and acquired date. The optimizer sells by lot. |
| Long-term | A lot held over a year. Its gain is taxed at `lt_gain_rate`, otherwise `st_gain_rate`. |
| Wash sale | Buying a bond within 30 days of selling it at a loss, which disallows the loss. |
| Loss sale | A sale of a bond at a loss in the last 30 days: the `loss_sales` dataset, which `no_wash_sales` reads. |
| Benchmark, target | Weights by bond that a portfolio is measured against, or moves toward. |
| Weight | Market value as a fraction of the portfolio's pre-trade value, cash included. |
| Par | Face amount. Prices are per 100 par, and dirty. |
| Increment | Trades are whole multiples of it. |
| Minimum piece | The smallest trade allowed. |
| Minimum remaining | The smallest amount a partial sale may leave in a lot. |
| Offering | Par of a bond on offer today. A bond with no offering cannot be bought. |
| Signal | Expected annual pre-tax return per bond, from the signals API. |
| Prep | What every strategy needs from the datasets, before the rules: joins, what missing data means, checks across datasets. `build_market`, `build_portfolios` and `Market.inputs_for` in `inputs.py`. |
| Strategy | The JSON file that says how to optimize: rules, objective, constraints, solve. |
| Rule | A pre-solve edit to what may be bought or to expected returns. |
| Kind | The `kind` that names a rule, term or constraint in a strategy file, such as `min_rating`. |
| Snapshot | A run's saved request, inputs and results: see [snapshot files](snapshots.md). |
