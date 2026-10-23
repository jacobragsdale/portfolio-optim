"""The docs that ship with optim hang together: every page the skills and the contract point at exists, and every
relative link and anchor in the docs resolves."""

import re
from pathlib import Path

import pytest

from optim.cli import main

PACKAGE = Path(__file__).parent.parent / "src" / "optim"
PAGES = sorted((PACKAGE / "docs").rglob("*.md"))
LINKED = [*PAGES, Path(__file__).parent.parent / "README.md", Path(__file__).parent.parent / "docs" / "design.md"]


def anchors(page: Path) -> set[str]:
    """GitHub's anchors for a page's headings."""
    return {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in re.findall(r"^#+ (.+)$", page.read_text(encoding="utf-8"), re.MULTILINE)}


def test_every_page_the_skills_and_the_contract_name_exists(capsys: pytest.CaptureFixture[str]) -> None:
    guidance = [*(PACKAGE / "skills").glob("*/SKILL.md"), PACKAGE / "templates" / "contract.md"]
    named = {page for file in guidance for page in re.findall(r"optim docs ([\w/-]+)", file.read_text(encoding="utf-8")) if page != "PAGE"}

    for page in sorted(named):
        assert main(["docs", page]) == 0, page
    assert named
    assert "# Build, replay and sweep your first model" in capsys.readouterr().out


@pytest.mark.parametrize("page", LINKED, ids=lambda page: page.name)
def test_every_relative_link_and_anchor_resolves(page: Path) -> None:
    for target, anchor in re.findall(r"\]\((?!https?://)([^)#\s]*)(?:#([^)\s]+))?\)", page.read_text(encoding="utf-8")):
        linked = (page.parent / target).resolve() if target else page
        assert linked.exists(), f"{page.name} links to {target}"
        if anchor:
            assert anchor in anchors(linked), f"{page.name} links to {target}#{anchor}"
