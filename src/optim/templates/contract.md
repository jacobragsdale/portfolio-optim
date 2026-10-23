## The optim contract

Written by `optim sync` from optim {version}. Edit outside this block; `uv run optim sync` rewrites it, and a contract
test fails when it does not match the installed optim.

This repo holds models that run on `optim`. `optim` loads each model's data in the parent process, decides each
portfolio in worker processes, rounds each decision to its securities' trading increments, checks it, saves the run as
a snapshot that replays, and writes accepted orders in production. A model holds only what is particular to it.

### What a model is

A model is a module listed in `pyproject.toml` under `[project.entry-points."optim.models"]`. It exports:

| Name | Runs | What it is for |
|---|---|---|
| `Params(optim.Params)` | Validated once per run | The model's parameters. Strict: unknown keys and coercion are refused. |
| `Data(optim.Datasets)` | Loaded once per run | The datasets it reads, as typed attributes; `holdings` and `securities` are always there. |
| `decide(prepared, shared, params)` | Once per portfolio | Returns the portfolio's `optim.Decision`. |
| `prepare(data, params)` | Once per run, optional | Work every portfolio shares. Without it, `shared` is the run's `Data`. |
| `prepare_portfolio(portfolio, shared, params)` | Once per portfolio, optional | The model's rules. Without it, `prepared` is the portfolio's slice of `Data`. |
| `reserve(shared, decision)` | After each accepted decision, optional | Makes portfolios decide one at a time; returns the shared state the next one sees. |
| `order(prepared, shared, params)` | Once, with `reserve`, optional | The portfolio ids in the order they decide. |
| `check(prepared, decision)` | Once per decision, optional | The model's own acceptance: the reasons to reject. |
| `Metrics(optim.Metrics)` | Optional | The numbers decisions report in `metrics`, for sweeps. |

A cvxpy problem goes through `optim.cvxpy.solve(problem, params.solve, securities=..., buy=..., sell=...)`, which
returns the `Decision`; a model with no solver returns `optim.Decision(buy={...}, sell={...})`.

### Rules to keep

- Model code does no I/O: no files, databases or HTTP. Data arrives in `Data`; a contract test fails on an import that
  can reach a source, the loader, the cache, outputs, a database driver or an HTTP client.
- Every query takes only the as-of date and the portfolio ids. Params never change what is loaded: select rows after
  loading instead, so a sweep can run every variant from one snapshot.
- Rules are plain functions in the model's `rules.py`, tested without a solver, and named in its `README.md`.
- Quantities are positive floats in the holdings' units. The core rounds them and rejects a decision that sells more
  than it holds, trades an unknown security or overdraws cash; `check` adds the limits the core cannot see.
- Log with `logging.getLogger(__name__)`, never `print`. Document every field with a docstring under it.
- Keep each model's `README.md` true: what it maximizes, its constraints, its data and its known limits.

### Where things are

| Path | Holds |
|---|---|
| `src/<package>/<model>/` | The model's module, its `rules.py`, its own datasets and its `README.md`. |
| `params/<model>/default.json` | Its default params; other files beside it are variants. |
| `tests/<model>/` | Its behaviour tests, and `fixture/`: one file per dataset and `fixture.json`, which the contract tests decide on. |
| `tests/e2e/` | The QA suite, marked `e2e`, which runs only against QA. |

### Commands

```sh
uv run pytest                                    # behaviour and contract tests
uv run ruff check . && uv run ty check           # lint and types
uv run optim describe MODEL                      # params schema, datasets and metrics, as JSON
uv run optim run MODEL --params params/MODEL/default.json --data tests/MODEL/fixture --as-of DATE
uv run optim replay RUN_ID [--params FILE]       # decide a saved run again with this code
uv run optim sweep RUN_ID params/MODEL/sweep     # one table: a row per variant and portfolio
uv run optim regress MODEL --snapshots ROOT      # replay recent runs; lists every trade that changed
uv run optim sync                                # rewrite this block and the optim-* skills
uv run optim docs [PAGE]                         # optim's own docs: tutorial, how-to, reference, explanation
```

The `optim-*` skills cover starting a new model, porting a legacy optimizer, running a research sweep and triaging an
e2e failure; each points at a page of `optim docs`.
