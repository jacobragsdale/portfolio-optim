# How to run a model from your IDE

Run a model by running a file, to try a change or to stop in a debugger, with nothing to type in a terminal. Each
model's `__main__.py` runs it as `optim run` does, and by default decides the model's fixture with its default params.

Prerequisites:

- A model repo with `uv sync` run, and your IDE's Python interpreter set to the repo's `.venv`.
- A `.env` at the repo's root: copy `.env.example`, which sets `OPTIM_SNAPSHOT_ROOT`, all that a run of the fixture
  needs.
- The model's `__main__.py`, beside its `__init__.py`. `optim new` writes one for every model. A model made without
  one gets it by copying another model's: the file names no model.

To run a model on its fixture:

1. Open `src/PACKAGE/MODEL/__main__.py` and run it as a Python file, or debug it.

   `PACKAGE` is the repo's package and `MODEL` the model's module, such as `src/ladder/ladder/__main__.py`. The run
   logs as text, then prints what `optim run` prints:

   ```text
   run 01a16705-dc39-766a-91e1-8bbcb940a84f
   portfolio_id   status  trades  solved  objective    stop  seconds
             P1 accepted       2    True   0.004592 optimal 0.038965
             P2 accepted       3    True   0.006723 optimal 0.030192
   ```

   Your run id and times will differ. A rejected portfolio is printed with its reasons below the table.
2. Optional: to stop in your own code, set a breakpoint in a rule or in `decide` and debug the file. A debugger stops
   there while the run decides in one process, which it does unless `OPTIM_WORKERS` is above 1.

The file runs from the repo's root, whatever directory your IDE starts it in, so a relative path is the repo's. It reads
the repo's `.env` into the environment, where a variable already set wins. An error raises with its traceback, rather
than being logged as the command line logs it, so a debugger stops where it happened.

To run something other than the fixture, change the call in `__main__.py`:

- For other params, pass a params file: `optim.ide.run(__file__, params="params/MODEL/wider.json")`.
- For live data, pass the portfolios, and the business date unless it is today in New York:
  `optim.ide.run(__file__, portfolios=["P1", "P2"], as_of="2026-10-16")`. The run reads each dataset from its source,
  so `.env` must configure every source the model reads, as the [settings reference](../reference.md#settings) names
  them.
- To decide a saved run again with the code you have now, such as a production run whose snapshot you can read, pass
  its id under `OPTIM_SNAPSHOT_ROOT`, or its snapshot's path: `optim.ide.replay(__file__, "RUN_ID", portfolios=["P1"])`.

Keep the call under `if __name__ == "__main__":`. Each worker process imports the file that started the run, and the
guard keeps it from starting a run of its own. Keep everything else out of `__main__.py` too: it is the one module of
a model that may reach I/O, so the contract tests' import check leaves it out.

To confirm it worked, find the run's snapshot under `OPTIM_SNAPSHOT_ROOT`, `snapshots/RUN_ID/` with the `.env` from
`.env.example`. From a terminal, `uv run python -m PACKAGE.MODEL` runs the same file.

For every argument of `run` and `replay`, see the [`optim.ide` reference](../reference.md#optimide).
