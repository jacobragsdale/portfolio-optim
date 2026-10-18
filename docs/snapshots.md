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

What the run was asked, when it ran, with which code, and where its data came from: nothing the snapshot's other files
hold. `RunInfo` in `src/optim/jobs/__init__.py` is its model.

| Key | Value |
|---|---|
| `run_id` | The run's id, which names the directory: letters, digits, `.`, `_` and `-`, such as a scheduler's numeric id passed with `--run-id`, or a UUIDv7. |
| `job` | The request's job, such as `optimize` or `hypothetical`. A replay keeps the job of the run it replays. |
| `as_of_date` | The business date, `YYYY-MM-DD`. |
| `started_at` | When the run started, before it loaded anything. Times are UTC, ISO 8601. |
| `loaded_at` | When every dataset had loaded; for a replay, when its snapshot had been read and its portfolios prepared. |
| `finished_at` | When the last solve finished, just before the snapshot was written. |
| `code` | `git_sha`: the commit, from `OPTIM_GIT_SHA` in an image, else the checkout's `HEAD`. `image`: from `OPTIM_IMAGE`, which the Job sets. `dirty`: whether the checkout had changes not yet committed; null in an image. Each is null when unknown. |
| `labels` | `key=value` pairs the run was started with (`--label`), such as `purpose=production` or Airflow's DAG run. |
| `workers` | Solver processes. A solve that stops at its time limit depends on how many solves shared the machine. |
| `datasets` | Per dataset loaded: `read`, which is `live` (not cached), `hit`, `miss` (read live, then cached) or `refresh` (by `optim warm`); and `fetched_at`, when its source was read: for a hit, when the cache entry was filled; for a dataset loaded in batches, the oldest batch's. Null for a replay. |
| `request` | The job's request as JSON: `job`, `strategy`, `as_of_date`, and the job's own fields: for `optimize`, `portfolios` and `holdings` (`working` or `sod`); for `hypothetical`, `portfolios`, each with its details, sleeves, holdings, controls and cash flows as rows. |
| `replay` | A replay only: `of`, the run it replayed, and `portfolios`, the subset it solved, or null for all of them. |

Replay reads `run_id`, `request` and `replay`. A run that fails before it saves has no snapshot: its logs, filtered on
its `run_id`, tell what happened.

## `inputs/`

One Parquet file per dataset, named after its `Dataset` (`securities.parquet` for `SECURITIES`), with exactly the
fields of its row model, in its order and dtypes, as loaded. `Decimal` fields, such as prices, quantities and cash
flows, are Parquet decimals, exactly as the source returned them. The row models are in `src/optim/datasets/`: the
market's `securities`, `universe`, `signals` and `benchmarks`, and the portfolios' `portfolios`, `sleeves`, `holdings`,
`controls` and `cash_flows`. A `hypothetical` run loads no portfolio, so its portfolio files have no rows. Replay
validates each file as a load validates the dataset: fields, values, key and cross-row check. A file that fails names
the column and the row; a file named after no dataset fails too.

## Results

Four files, each with exactly the fields of its row model in `src/optim/optimizer/results.py`, in that order, even when
it has no rows. Each field's docstring there says what the column holds. Dollar amounts are rebuilt from the exact
trades, not read from the solver.

| File | Row model | One row per |
|---|---|---|
| `trades.parquet` | `Trade` | trade, by sleeve and security: `side`, exact `quantity`, `value`, and for a sale whether it `liquidates` the holding and whether a control made it `mandatory` |
| `positions.parquet` | `Position` | line held after trading, by sleeve and security; cash is in the summary |
| `summary.parquet` | `Summary` | solve, in the order the job made them; its numbers are null when the solve failed |
| `violations.parquet` | `Violation` | acceptance check a result failed |

A summary's `status` is `accepted`; `rejected: [...]`, naming the checks the result failed; or why the portfolio did
not solve, such as `infeasible; before any trade it breaks ...` or `limit with no solution (...)`. `stop` is why the
solver stopped (`optimal`, `limit` or `imprecise`), and `gap` and `nodes` are the solver's, for integer solves. `base`
is the value weights are fractions of, and `cash` is cash after trading and cash flows.

A violation's `check` is one of:

| Check | Fails when | `excess` |
|---|---|---|
| `sizes` | A trade is not a size the market trades: a buy that is not whole increments of at least the minimum piece, or that takes a security's buys across its lines past what is available; a partial sale that is not whole increments of at least the minimum piece, or that leaves less than the minimum remaining. | Lines |
| `controls` | A line is bought that may not be, or sold that may not be, or a mandatory sale is not of the whole holding. | Lines |
| `cash` | Cash after trading and cash flows is negative. | Weight |
| `model` | The model's cash or holdings after trading disagree with those rebuilt from the trades. | Weight |
| `solver` | The solver doubts its answer, an LP stopped at its time limit, or an integer answer's gap is past the strategy's `max_gap`, whether the solver called it optimal or stopped at its time limit. | Gap past `max_gap`; 1 when there is no gap to judge by |
| `I:KIND`, such as `2:max_turnover` | The strategy's constraint at position `I` does not hold at the exact trades. | The constraint's units: weight for most, the analytic's for `exposure` |

Weights and constraint excesses count only above a tolerance of 1e-6, $1 per $1mm of base value. A rejected result is
saved, and makes the run exit nonzero.

To compare two runs of `optimize`, join their `summary.parquet` files on `portfolio_id`.
