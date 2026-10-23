# optim

The run loop that a firm's portfolio models share. A model is one module that declares its parameters and datasets and
decides each portfolio's trades in a pure function; `optim` runs everything else the same way for every model: loading
in the parent process, deciding in parallel worker processes, rounding to trading increments, acceptance, snapshots
that replay, research sweeps, logging and saving orders. Each model lives in its own repo, which depends on `optim`.

## Start a model repo

You need [uv](https://docs.astral.sh/uv/) and git. `INDEX_URL` is the internal package index `optim` is published to.

```sh
uvx --index INDEX_URL optim new repo ladder --index INDEX_URL
cd ladder
uv run pytest
```

The new repo holds one model, copied from the `cvxpy` example, with its params, a fixture, tests and a `__main__.py`
that runs it from an IDE, plus a `Dockerfile`, an `azure-pipelines.yml` stub, and the agent files that `optim sync`
keeps current. Its tests include `optim`'s contract tests for every model it lists. The second `--index` is required:
PyPI has an unrelated package named `optim`, which a repo without it would install.

## Documentation

The docs ship with the package, so a repo reads the ones for the `optim` it installed: `uv run optim docs` lists them,
and `uv run optim docs PAGE` prints one.

- [Build, replay and sweep your first model](src/optim/docs/tutorial.md): from nothing to a run, a replay and a sweep,
  in about fifteen minutes.
- How-to guides: [add a dataset](src/optim/docs/how-to/add-a-dataset.md),
  [write a rule](src/optim/docs/how-to/write-a-rule.md),
  [run a model from your IDE](src/optim/docs/how-to/run-from-your-ide.md),
  [decide portfolios one at a time](src/optim/docs/how-to/decide-portfolios-in-turn.md),
  [port a legacy model](src/optim/docs/how-to/port-a-legacy-model.md), and
  [run a model in QA](src/optim/docs/how-to/run-in-qa.md).
- [Reference](src/optim/docs/reference.md): the model contract, the command line, the settings, the snapshot files and
  the pytest plugin.
- [How optim is put together](src/optim/docs/explanation.md): why the core owns the loop, why model code does no I/O,
  and how runs replay.
- [The optim framework](docs/design.md): the design this package implements, and what was decided against.

## Develop optim

```sh
uv sync
uv run pre-commit run --all-files   # ruff, ty, the lockfile and the import contract
uv run pytest                        # the core's tests, which also run both examples
```

`uv sync` also installs COPT, which the tests run without a license, size-limited. [AGENTS.md](AGENTS.md) maps every
module and lists the rules to keep. The firm's pipeline builds and publishes the package to the internal index when a
version is tagged; the version is in `pyproject.toml`.
