# How to run a model in QA

Run each model of a repo against the QA environment, through the deployed image's `optim`, and triage what fails. The
e2e suite in `tests/e2e/` does it, and `optim`'s `optim_qa` fixture refuses to run it anywhere but QA.

Prerequisites:

- The repo's image, which the pipeline builds from its `Dockerfile` and deploys to QA, or a checkout of the repo with
  `uv sync` run. A laptop counts as QA when every setting points at QA.
- QA's `OPTIM_*` settings, with read-only credentials for its sources, in a file such as `qa.env`. The
  [settings reference](../reference.md#settings) names each variable; the environment's owner has their values.

To run the e2e suite:

1. In `pyproject.toml`'s `[tool.pytest.ini_options]`, which the repo already has, list what the suite may write to,
   exactly as the QA environment sets them: `OPTIM_SNAPSHOT_ROOT`, and `OPTIM_ORDERS__TABLE` and `OPTIM_CACHE_ROOT`
   where `qa.env` sets them.

   ```toml
   [tool.pytest.ini_options]
   optim_qa_markers = ["qa"]
   optim_e2e_writes = ["//qa-share/optim/snapshots", "qa.orders"]
   ```

   `optim_qa_markers` is text, not a pytest marker: every QA host, connection string, URL and root must contain one of
   its entries, ignoring case. Change it if QA's names carry another.
2. Make `tests/e2e/sql/portfolios.sql`, which the repo already has, pick the portfolios to test by what they hold, such
   as three that hold bonds, rather than by fixed ids, which drift as QA's data changes. It runs on SQL Server, with no
   binds, and must be a `SELECT` that returns `portfolio_id`.
3. Point the suite at the deployed image: set `optim_e2e_command`, in the same table, to the command that runs its
   `optim`, such as `["docker", "run", "--rm", "--env-file", "qa.env", "IMAGE"]`; the image's entrypoint is `optim`.
   The suite appends `run MODEL --params params/MODEL/default.json --portfolios ...`, and later `replay RUN_ID`, and runs
   the command from the repo's root, so the relative params path resolves in the image too, whose workdir holds the
   repo. Without the setting, the suite runs this checkout's `optim`.
4. Set QA's settings and run the suite:

   ```sh
   uv run --env-file qa.env pytest -m e2e
   ```

   Replace `qa.env` with the file that holds QA's `OPTIM_*` settings.

You write no test code: `tests/e2e/test_smoke.py` gives every model the repo lists a smoke case. The run completes,
each portfolio is accepted or rejected with reasons, and replaying the snapshot gives the same trades for the decisions
that did not stop at a time limit. The suite passes when every case does, with one passed test per model.

If the suite reports `refusing to run the e2e suite outside QA`, a configured source or destination lacks the marker,
or a write is not on `optim_e2e_writes`. The message names each one. Fix the settings, never the safeguard.

## How to triage an e2e failure

Sort the failure before changing anything:

1. **Refused to run.** The environment is not QA's, or writes outside the allowlist. Fix the environment.
2. **The run did not complete.** The failure message ends with the run's stderr, its JSON log lines; every line
   carries the run's `run_id`. The `error` record names the root cause, the notes added on the way up, and `where` it
   surfaced. A source that timed out, or refused a login, is a QA outage: rerun once QA is back.
3. **A portfolio was rejected without reasons.** That is a bug in the model or in `optim`: report it with the run id.
4. **A portfolio was rejected with reasons**, which the smoke case accepts. To see why, read the snapshot's
   `reasons.parquet`, then decide the portfolio from Python:

   ```python
   from optim.research import trial
   from optim.run import open_snapshot
   from optim.storage import Root

   found = trial(open_snapshot(Root.at("//qa-share/optim/snapshots/RUN_ID")), "PORTFOLIO_ID")
   print(found.outcome.reasons, found.prepared)
   ```

   Replace `RUN_ID` and `PORTFOLIO_ID`; `Root.at` takes the run's snapshot directory, and `found.prepared` is what the
   model's `prepare_portfolio` returned for the portfolio. If the data is wrong, as when QA's prices are stale, it is data drift: report
   it to QA's owner. If the model is wrong, fix it, with a behaviour test that reproduces the case first.
5. **The replay differs.** A decision that stopped at its time limit may stop elsewhere and is left out of the
   comparison. Any other difference means the model reads something outside its snapshot, such as the clock or a
   random number without a seed: make it read the run's `as_of_date`, or a seed from its params.

Report the run id, the portfolios and their reasons. Never weaken a check or an assertion to make the suite pass.

For the ini options and the `optim_qa` fixture's methods, see the [reference](../reference.md#the-pytest-plugin).
