# TODO

## Dashboards on snapshot data (Streamlit)

For the quant developer first: researching and testing strategies, and reviewing the production runs of the SMA
product. Build it, what-if included and hosting not, before handing the engine over: seeing runs makes the pitch. Build
it after the engine changes it reads: replayable jobs, a schema for result frames, and the code version in `run.json`.

### Structure

```
snapshots/RUN_ID/      run.json, inputs/*.parquet, results     written by jobs, never changed afterwards
      │
src/optim/views.py     pure functions: snapshot -> the frames people look at
      │                built on build_inputs, apply_rules and the results schema; tested in pytest
dashboards/            Streamlit pages: pick a run or portfolio, call a view, plot it
```

- No business logic in pages. A page that recomputes tax or exposures in its own pandas drifts from the engine; views
  call the engine's own prep and rules instead, so a change there shows on every page without editing it.
- Result frames get row models like the datasets have, with a description on every field. Tables take column labels
  and help text from them, so a new field appears with its explanation.
- CI runs every view on the snapshot the job tests write, and each page headless with Streamlit's `AppTest`, so a
  renamed column fails a test rather than a page.
- Snapshots never change once written: cache each run by `run_id` with no expiry. Only the runs list needs a ttl.
- Dependencies go in a `dashboard` dependency group (`streamlit`, `duckdb`), so the Job image does not carry them.

### First pages

- Runs: one row per run, with its job, date, strategy, portfolios, how many solved cleanly, and total tax and turnover.
- Portfolio: holdings before and after with analytics; weights against the benchmark; duration, sector and rating
  exposures before, after and at target; trades with their lot, gain and tax; what each rule excluded.
- Compare: two runs side by side, such as strategy A against B, or a replay against its original.
- Market: the buyable universe, signal coverage, and what prep left out.
- What-if: pick a run and a portfolio, edit the strategy, and re-solve that one portfolio in the dashboard's process,
  beside the original. It must not save a snapshot (the volume is mounted read-only): a view that runs prep, the rules
  and the solve directly, as replay does, and returns the result.

### History

Keep 2 to 4 weeks of runs on the snapshot volume. An Airflow task, not built yet, archives older snapshots to the
datalake or Snowflake and deletes them from the volume. Dashboards read only the volume. Across runs, DuckDB reads
`snapshots/*/summary.parquet` in one query, and `union_by_name` copes with columns added over time.

### Hosting on AKS (not decided)

No authentication is needed. The simplest setup:

- A second target in the `Dockerfile` that installs the `dashboard` group and runs
  `streamlit run dashboards/app.py --server.headless true`.
- A Deployment with one replica that mounts the `optim-snapshots` claim read-only (Azure Files allows many readers
  beside the writing Jobs), and a ClusterIP Service on port 8501.
- Reach it with `kubectl port-forward svc/optim-dashboard 8501:8501` at first. For a stable URL on the network, use an
  internal LoadBalancer (`service.beta.kubernetes.io/azure-load-balancer-internal: "true"`) or the cluster's ingress.
- Size it for what-if solves: one solve takes up to its time limit (30 s by default) and a few hundred MB, so start at
  2 CPU and 2 GiB.
