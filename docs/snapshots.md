# Snapshot files

Every run saves a snapshot: a directory `OPTIM_SNAPSHOT_DIR/RUN_ID` holding what the run was asked, the datasets it
loaded, and what it returned. A replay saves its snapshot beside the one it replays. The directory appears only once it
is complete, and nothing changes it afterwards. `optim replay` reads it, and so does anything that reports on runs.

```text
RUN_ID/
├── run.json
├── inputs/
│   └── <dataset>.parquet        one per dataset the run loaded
├── trades.parquet
├── positions.parquet
├── summary.parquet
└── violations.parquet
```

## `run.json`

| Key | Value |
|---|---|
| `run_id` | The run's id, which names the directory. |
| `job` | `optimize`, `model_portfolio` or `transition`. A replay keeps the job of the run it replays. |
| `as_of_date` | The business date, `YYYY-MM-DD`. |
| `code` | `git_sha` and `image`: the commit and the image that ran it, from `OPTIM_GIT_SHA` and `OPTIM_IMAGE`. Both are null for a run outside an image unless they are set. |
| `request` | The job's request as JSON: `job`, `strategy`, `as_of_date`, and the job's own fields, such as `portfolios`. |
| `replay` | A replay only: `of`, the run it replayed, and `portfolios`, the subset it solved, or null for all of them. |

Replay reads `run_id`, `request` and `replay`.

## `inputs/`

One Parquet file per dataset, named after its `Dataset` (`securities.parquet` for `SECURITIES`), with exactly the
fields of its row model, in its order and dtypes, as loaded. The row models are in `src/optim/datasets/`. Replay
validates each file as a load validates the dataset: fields, values, key and cross-row check. A file that fails names the
column and the row; a file named after no dataset fails too.

## Results

Each result file has exactly the fields of its row model in `src/optim/optimizer/results.py`, in that order, even
when it has no rows.

`trades.parquet` (`Trade`), one row per trade:

| Field | Type | Meaning |
|---|---|---|
| `portfolio_id` | str | |
| `strategy` | str | The strategy's name; for a transition, with its tax budget, as `core\|tax<=25000`. |
| `cusip` | str | |
| `lot_id` | str, null | The tax lot a sale comes from; null for a buy. |
| `side` | category | `buy` or `sell`. |
| `par` | int | Face amount traded, whole dollars. |

`positions.parquet` (`Position`), one row per bond held after trading:

| Field | Type | Meaning |
|---|---|---|
| `portfolio_id` | str | |
| `strategy` | str | |
| `cusip` | str | |
| `par` | int | Face amount held, whole dollars. |
| `weight` | float | Market value as a fraction of the pre-trade value, cash included. |

`summary.parquet` (`Summary`), one row per solve, in the order the job made them. The numbers are null when the solve
failed.

| Field | Type | Meaning |
|---|---|---|
| `portfolio_id` | str | |
| `strategy` | str | |
| `status` | str | `ok`; `rounding broke [...]` with the constraints broken at the exact trades; or why it did not solve, such as `infeasible; before any trade it breaks ...`. |
| `objective` | float | The solver's optimum before snapping or rounding, as a fraction of value. |
| `gap` | float | Integer solves: how far the optimum may be from the best possible. Null for an LP. |
| `value` | float | Pre-trade value, cash included, $. |
| `cash` | float | Cash after trading, $. |
| `turnover` | float | Value bought plus value sold, $. |
| `tax` | float | Tax on realized gains, $; negative when harvesting losses. |
| `cost` | float | Trading costs, $. |
| `income` | float | Expected return over a year, $, as the strategy's rules left it: after tax when they include `after_tax`. |
| `tracking` | float | L1 distance of the weights from the target, including target weight outside the model. |
| `seconds` | float | Time to build, solve and check the portfolio. |

`violations.parquet` (`Violation`), one row per constraint broken at the exact trades, as rounding an LP can do:

| Field | Type | Meaning |
|---|---|---|
| `portfolio_id` | str | |
| `strategy` | str | |
| `constraint` | str | Its place in the strategy and its kind, such as `3:max_turnover`; `bounds` for the trade sizes and the cash floor. |
| `excess` | float | The worst excess, in the constraint's own units: weights for most. |

A result with a broken constraint is saved, and makes the run exit nonzero.

To compare two runs, join their `summary.parquet` files on `portfolio_id`. For a transition, which has a row per tax
budget, join on `portfolio_id` and row order instead.
