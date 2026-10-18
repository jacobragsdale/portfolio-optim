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
| `run_id` | The run's id, which names the directory. |
| `job` | The request's job, such as `optimize`. A replay keeps the job of the run it replays. |
| `as_of_date` | The business date, `YYYY-MM-DD`. |
| `started_at` | When the run started, before it loaded anything. Times are UTC, ISO 8601. |
| `loaded_at` | When every dataset had loaded; for a replay, when its snapshot had been read and its portfolios prepared. |
| `finished_at` | When the last solve finished, just before the snapshot was written. |
| `code` | `git_sha`: the commit, from `OPTIM_GIT_SHA` in an image, else the checkout's `HEAD`. `image`: from `OPTIM_IMAGE`, which the Job sets. `dirty`: whether the checkout had changes not yet committed; null in an image. Each is null when unknown. |
| `labels` | `key=value` pairs the run was started with (`--label`), such as `purpose=production` or Airflow's DAG run. |
| `workers` | Solver processes. A solve that stops at its time limit depends on how many solves shared the machine. |
| `datasets` | Per dataset loaded: `read`, which is `live` (not cached), `hit`, `miss` (read live, then cached) or `refresh` (by `optim warm`); and `fetched_at`, when its source was read: for a hit, when the cache entry was filled; for a dataset loaded in batches, the oldest batch's. Null for a replay. |
| `request` | The job's request as JSON: `job`, `strategy`, `as_of_date`, and the job's own fields, such as `portfolios`. |
| `replay` | A replay only: `of`, the run it replayed, and `portfolios`, the subset it solved, or null for all of them. |

Replay reads `run_id`, `request` and `replay`. A run that fails before it saves has no snapshot: its logs, filtered on
its `run_id`, tell what happened.

## `inputs/`

One Parquet file per dataset, named after its `Dataset` (`securities.parquet` for `SECURITIES`), with exactly the
fields of its row model, in its order and dtypes, as loaded. The row models are in `src/optim/datasets/`. Replay
validates each file as a load validates the dataset: fields, values, key and cross-row check. A file that fails names the
column and the row; a file named after no dataset fails too.

## Results

Four files, each with exactly the fields of its row model in `src/optim/optimizer/results.py`, in that order, even when
it has no rows. Each field's docstring there says what the column holds.

| File | Row model | One row per |
|---|---|---|
| `trades.parquet` | `Trade` | trade; a sale names its tax lot and carries the gain and tax it realizes |
| `positions.parquet` | `Position` | bond held after trading |
| `summary.parquet` | `Summary` | solve, in the order the job made them; its numbers are null when the solve failed |
| `violations.parquet` | `Violation` | constraint broken at the exact trades, as rounding an LP can do |

A summary's `status` is `ok`; `rounding broke [...]`, naming the constraints broken at the exact trades; or why the
portfolio did not solve, such as `infeasible; before any trade it breaks ...`. A constraint is named by its place in the
strategy and its kind, such as `2:max_turnover`, or `bounds` for the trade sizes and the cash floor. A result with a
broken constraint is saved, and makes the run exit nonzero.

To compare two runs of `optimize`, join their `summary.parquet` files on `portfolio_id`.
