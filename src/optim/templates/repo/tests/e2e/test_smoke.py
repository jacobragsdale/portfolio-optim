"""The e2e suite: each model's smoke case, run by the deployed image's `optim` against QA. `uv run pytest -m e2e` runs
it; the default run leaves it out. optim's `optim_qa` fixture refuses to run unless every source and destination is
QA's, and the suite writes only to the allowlist in `pyproject.toml`. Test portfolios are picked by what they hold."""

from pathlib import Path

import pytest
from optim.pytest_plugin import QA, listed

REPO = Path(__file__).parents[2]
pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("model", sorted(listed(REPO)))
def test_each_model_runs_decides_every_portfolio_and_replays(optim_qa: QA, model: str) -> None:
    portfolios = optim_qa.portfolios(Path(__file__).parent / "sql" / "portfolios.sql")

    optim_qa.smoke(model, Path("params") / model / "default.json", portfolios)  # relative to the repo, as inside the image
