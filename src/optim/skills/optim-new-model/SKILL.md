---
name: optim-new-model
description: Start a new optim model in this repo, from the cvxpy or plain example, through to passing contract tests. Use when asked to add, create or scaffold a model, optimizer or strategy that runs on optim.
---

# Start a new optim model

1. Read the tutorial: `uv run optim docs tutorial`. It builds a model from nothing to a run, a replay and a sweep.
2. Add the model: `uv run optim new model NAME --from cvxpy` for a solver, or `--from plain` for plain Python. It
   writes `src/<package>/<name>/`, with `__main__.py`, which runs the model, `params/<name>/default.json`,
   `tests/<name>/` with a fixture, and the entry point.
3. Change it from the inside out: `Params`, then `Data` (add a dataset: `uv run optim docs how-to/add-a-dataset`),
   then the rules in `rules.py` (`uv run optim docs how-to/write-a-rule`), then `decide` and `check`.
4. Keep the fixture, the default params and the model's `README.md` true as you go, and write a behaviour test per
   rule, on a few rows built with `optim.testing`. To see a change work, run the model on its fixture:
   `uv run python -m <package>.<name>`, once `.env` exists (copy `.env.example`).
5. Done when `uv run pytest`, `uv run ruff check .` and `uv run ty check` pass: the contract tests decide every
   portfolio of the fixture and refuse any import that reaches I/O.

The contract is in this repo's `AGENTS.md`; `uv run optim docs reference` holds every hook, setting and command.
