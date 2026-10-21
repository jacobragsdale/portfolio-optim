"""Mandatory trading controls per portfolio, from SQL Server: what may not be bought or sold, and what must be sold.
Prep applies them to every solve, whatever its strategy (see `optim.prep.controls`). Read live, with the portfolio's
holdings."""

from typing import Literal

from optim.data.dataset import Dataset
from optim.schema import Id, Row

type Action = Literal["no_buy", "no_sell", "hold", "must_sell"]


class Control(Row):
    portfolio_id: Id
    control_id: Id
    security_id: Id | None
    """The security it controls; null: every security in the portfolio."""
    action: Action
    """`no_buy`: may not be bought, as after a wash sale. `no_sell`: may not be sold at the optimizer's discretion.
    `hold`: neither. `must_sell`: the whole holding is sold, and nothing is bought back."""
    reason: str
    """Why, as the source records it, such as `wash_sale` or `client_restriction`; errors quote it."""


CONTROLS = Dataset("controls", Control, source="mssql", query="datasets/sql/controls.sql", key=("portfolio_id", "control_id"))
