# {{repo}}

Portfolio models that run on `optim`, which loads their data, decides each
portfolio, checks each decision and saves every run as a snapshot that replays. Each model's own README, beside its
module in `src/{{package}}/`, states what it does in business terms.

| Model | Module |
|---|---|
| `{{model}}` | `src/{{package}}/{{module}}/` |

## Set up

You need [uv](https://docs.astral.sh/uv/), which installs Python 3.14 on first use.

```sh
uv sync
uv run pytest
```

## Run a model

Copy `.env.example` to `.env` and set what this machine reads. A run on the model's fixture needs only
`OPTIM_SNAPSHOT_ROOT`, which it sets. To run a model from your IDE, run its `__main__.py`, such as
`src/{{package}}/{{module}}/__main__.py`, which decides its fixture; from a terminal:

```sh
uv run --env-file .env optim run {{model}} --params params/{{model}}/default.json --data tests/{{model}}/fixture --as-of 2026-10-16
uv run --env-file .env optim replay RUN_ID
```

`uv run optim docs` lists optim's own guides; `uv run optim docs tutorial` is the place to start.
