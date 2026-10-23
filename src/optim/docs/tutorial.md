# Build, replay and sweep your first model

In this tutorial, we will make a model repo, run its model on a small fixture, replay the run with wider limits, and
sweep both limits side by side, in about fifteen minutes. The model rebalances fixed income portfolios as an integer
program; you will not need to change its code.

Before you start, you need:

- [uv](https://docs.astral.sh/uv/), which installs Python 3.14 on first use, and git.
- A terminal: PowerShell on Windows, or any shell elsewhere. The commands are the same in both.
- The address of the firm's internal package index, where `optim` is published, which we call `INDEX_URL`. If the
  index needs a login, set uv's credentials for it first, as the index's owner describes. uv downloads Python itself, so
  it also needs to reach the internet, or a Python 3.14 your firm installs.

## Make the repo

Make a model repo named `ladder` from the `cvxpy` example:

```sh
uvx --index INDEX_URL optim new repo ladder --index INDEX_URL
cd ladder
```

Replace `INDEX_URL` with the index's address. The first `--index` is where `uvx` finds `optim`; the second is written
into the new repo, so that its own `uv sync` finds it too.

The last line of the output should be:

```text
created ladder: commit it once you have read it
```

`optim` has made the repo, run `git init`, and installed its dependencies; the first commit is yours, whenever you
like. Notice where the model is: its code is in `src/ladder/ladder/`, its default params in
`params/ladder/default.json`, and a small set of test data, its fixture, in `tests/ladder/fixture/`, one CSV file per
dataset.

## Run the tests

```sh
uv run pytest
```

The output should end with:

```text
pyproject.toml ....                                                      [ 36%]
tests/ladder/test_rules.py .......                                       [100%]

======================= 11 passed, 1 deselected in 1.44s =======================
```

The four tests on the `pyproject.toml` line come from `optim` itself. They check that the model's default params are
valid, that it accepts every portfolio of its fixture, that its code cannot reach a database or an API through its
imports, and that the repo's `AGENTS.md` matches the installed `optim`. The deselected test is the e2e suite, which
runs only against QA.

## Look at the model

Open `src/ladder/ladder/__init__.py`. A model is three things:

- `Params`, the settings a run takes from its params file, such as `max_weight`, the most of a portfolio's value in
  any one bond.
- `Data`, the datasets it reads: holdings and securities, which every model reads, and its own `signals` and
  `controls`.
- `decide`, which builds an integer program for one portfolio and solves it through `optim.cvxpy`. It maximizes
  expected income net of trading costs, as a fraction of the portfolio's value.

The file also defines `prepare`, `prepare_portfolio` and `check`, hooks that the how-to guides cover; you will not
need them here. The fixture has two portfolios. P1 holds $1mm of cash and 100,500 face of bond B, worth about $101,000,
or 9% of P1's value. P2 holds $500,000 of cash.

## Run the model on its fixture

A run needs to know where to save its snapshot, and reads that from the environment. Copy the example settings:

```sh
cp .env.example .env
```

The file sets `OPTIM_SNAPSHOT_ROOT=snapshots`, and a run on files needs nothing else.

Run the model on the fixture, for the fixture's date:

```sh
uv run --env-file .env optim run ladder --params params/ladder/default.json --data tests/ladder/fixture --as-of 2026-10-16
```

The run logs what it does as JSON lines, then prints a row per portfolio:

```text
run 01a166b6-22e5-7492-bd7b-67af298d5869
portfolio_id   status  trades  solved  objective    stop  seconds
          P1 accepted       2    True   0.004592 optimal 0.033753
          P2 accepted       3    True   0.006723 optimal 0.020721
```

Your run id and times will differ. Both portfolios are `accepted`: their trades may trade. The run has saved a snapshot
in `snapshots/RUN_ID/`, holding every dataset as it was loaded and every decision. Look at the trades:

```sh
uv run python -c "import pandas as pd; print(pd.read_parquet('snapshots/RUN_ID/trades.parquet'))"
```

Replace `RUN_ID` with your run's id. The output should be:

```text
  portfolio_id security_id  side  quantity  decided
0           P1           A   buy   54000.0  54000.0
1           P1           B  sell   46000.0  46000.0
2           P2           A   buy   24000.0  24000.0
3           P2           B   buy   24000.0  24000.0
4           P2           C   buy   24000.0  24000.0
```

Notice that P1 sells 46,000 of B, in whole lots of 1,000, which brings B under its 5% limit, and that neither
portfolio buys bond D, which is rated below `A-`, the `min_rating` in the params. `quantity` is what may trade, and
`decided` what the model decided, before `optim` rounded it to whole lots.

The same run starts from your IDE too: open `src/ladder/ladder/__main__.py` and run it, and it decides the fixture as
the command did, with the same `.env`, and prints the same table under a new run id.

## Replay the run with wider limits

Make a variant of the params that allows 20% in a bond and in an issuer. Copy `params/ladder/default.json` to
`params/ladder/wider.json`, and in the copy change `"max_weight": 0.05` to `"max_weight": 0.2` and
`"max_issuer_weight": 0.1` to `"max_issuer_weight": 0.2`.

Replay the run with the variant:

```sh
uv run --env-file .env optim replay RUN_ID --params params/ladder/wider.json
```

The output should look like this:

```text
run 01a166b6-4ff0-717f-95ad-746365648335
portfolio_id   status  trades  solved  objective    stop  seconds
          P1 accepted       2    True   0.016083 optimal 0.032369
          P2 accepted       2    True   0.012614 optimal 0.026301
```

The replay read nothing but the snapshot: it decided the same data again with the new params, and saved itself as a
new run beside the first. Notice that both objectives, the expected income net of costs, rose, because each portfolio
could put more in its best bonds.

## Sweep both variants

A sweep decides a saved run once per params file in a directory, and writes one table. Make the directory and put both
params files in it:

```sh
mkdir params/ladder/sweep
cp params/ladder/default.json params/ladder/sweep/default.json
cp params/ladder/wider.json params/ladder/sweep/wider.json
```

Sweep the first run:

```sh
uv run --env-file .env optim sweep RUN_ID params/ladder/sweep
```

The sweep prints a CSV table, a row per variant and portfolio; `--out sweep.csv` would write it to a file instead. Its
first columns should be:

```text
variant,portfolio_id,accepted,objective,turnover,trades,cash,...
default,P1,True,0.004591928739783337,101267.00000000001,2,991377.0,...
default,P2,True,0.006722724,72780.0,3,427220.0,...
wider,P1,True,0.01608340874325227,275165.00000000006,2,724835.0,...
wider,P2,True,0.012614245,124915.0,2,375085.0,...
```

The table also has each decision's solve time, the model's own metrics, `income` and `cost`, and the reasons for any
rejection. Notice that the wider limits trade more: P1's turnover goes from about $101,000 to $275,000.

## Next steps

You have built a model repo, run its model, replayed the run with new params and swept two variants. To make the model
your own, see [how to add a dataset](how-to/add-a-dataset.md) and [how to write a rule](how-to/write-a-rule.md), and to
run it as you change it, [how to run a model from your IDE](how-to/run-from-your-ide.md). For
every command and setting, see the [reference](reference.md). To understand why runs replay and model code does no I/O,
read [how optim is put together](explanation.md).
