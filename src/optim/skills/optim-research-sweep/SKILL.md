---
name: optim-research-sweep
description: Run a research sweep of params variants over a saved optim run and compare the outcomes, without touching production data. Use when asked to try, tune, compare or sweep parameters, constraints or limits of a model.
---

# Run a research sweep

1. Learn what the model takes and reports: `uv run optim describe MODEL` prints its params schema, datasets and
   metrics as JSON.
2. Pick a saved run to sweep, a run id under `OPTIM_SNAPSHOT_ROOT` or a snapshot's path; production snapshots are on
   shared storage.
3. Write one params file per variant in a directory, such as `params/MODEL/sweep/`, each a whole, valid params file.
   Params are strict: a misspelled key fails the sweep before anything decides.
4. Sweep: `uv run optim sweep RUN params/MODEL/sweep --out sweep.csv`. Each row is a variant and a portfolio: whether
   it was accepted, its objective, turnover, trades, cash, solve time, the model's metrics and its reasons.
5. To look inside one portfolio, decide it from Python with `optim.research.trial(open_snapshot(...), portfolio_id)`,
   which hands back what it was prepared with, the cvxpy problem and the outcome.

A sweep reads only the snapshot. `uv run optim docs reference` describes the sweep table, and the tutorial ends with
one.
