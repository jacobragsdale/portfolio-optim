---
name: optim-port-optimizer
description: Port a legacy portfolio optimizer onto optim as a model, keeping a PORTING.md log of every behaviour ported, changed or dropped. Use when asked to port, migrate, rewrite or replace an existing optimizer, strategy or rebalancer.
---

# Port a legacy optimizer

Follow `uv run optim docs how-to/port-a-legacy-model` step by step. In short:

1. Inventory the old code into `PORTING.md` before writing any: one row per behaviour, with where it lives, a
   decision (port, change, drop or ask) and why. Search the SQL, the fallbacks and the post-solve fixes, not only the
   optimizer.
2. Start the model from the closest example: `uv run optim new model NAME --from cvxpy` (or `plain`).
3. Port the data first, one dataset per query, then the rules, then the objective and constraints, each with its
   behaviour test named in the log.
4. Sanity-check on one day of the old system's inputs: run with `--data` on its export, compare outcomes per
   portfolio, and log every difference. The old system is a list of requirements, not results to match.

The port is done when every row has a decision and a test, the sanity check's differences are explained, and
`uv run pytest` passes.
