"""Pre-solve rules, each through the real loading path."""

import pytest

from optim.inputs import Rating
from optim.rules import AfterTax, MinRating, NoWashSales
from tests.builders import account, inputs, lot, security, tables


@pytest.mark.parametrize(("rating", "buyable"), [("AAA", True), ("A-", True), ("BBB+", False)])
def test_min_rating_keeps_bonds_at_or_above_it(rating: Rating, buyable: bool) -> None:
    data = tables([security(1, rating=rating)], accounts=[account()])
    assert MinRating(rating="A-").apply(inputs(data)).securities["buyable"].tolist() == [buyable]


@pytest.mark.parametrize(
    ("state", "tax_status", "kept"), [pytest.param("NY", "muni", 1.0, id="in-state-muni"), pytest.param("CA", "muni", 0.90, id="out-of-state-muni"), pytest.param(None, "taxable", 0.53, id="taxable")]
)
def test_after_tax_return_follows_the_account_brackets(state: str | None, tax_status: str, kept: float) -> None:
    data = tables([security(1, state=state, tax_status=tax_status)], accounts=[account(state="NY", federal_rate=0.37, state_rate=0.10)], expected_return=0.04)
    assert AfterTax().apply(inputs(data)).securities["expected_return"].iloc[0] == pytest.approx(0.04 * kept, abs=1e-12)


@pytest.mark.parametrize(("cost_price", "buyable"), [pytest.param(110.0, False, id="held-at-loss"), pytest.param(90.0, True, id="held-at-gain")])
def test_no_wash_sales_blocks_buying_what_is_held_at_a_loss(cost_price: float, buyable: bool) -> None:
    data = tables([security(1, price=100.0)], accounts=[account()], lots=[lot(1, "L1", cost_price=cost_price)])
    assert NoWashSales().apply(inputs(data)).securities["buyable"].tolist() == [buyable]
