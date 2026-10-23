"""Run this model by running this file, from an IDE, or with `uv run python -m optim.examples.plain`. It runs as
`optim run` does, from the repo's root and with the repo's `.env`, and decides every portfolio of the model's fixture
with its default params. To run other params, live data or a saved run, change the call:

    optim.ide.run(__file__, params="params/MODEL/wider.json")
    optim.ide.run(__file__, portfolios=["P1", "P2"], as_of="2026-10-16")  # from the sources .env configures
    optim.ide.replay(__file__, "RUN_ID", portfolios=["P1"])  # a saved run, decided again with this code

`uv run optim docs how-to/run-from-your-ide` says more."""

import optim.ide

if __name__ == "__main__":  # each worker process imports this file too, and must not start a run of its own
    optim.ide.run(__file__)
