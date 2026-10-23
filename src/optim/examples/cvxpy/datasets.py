"""The datasets this model reads besides the shared holdings and securities: expected returns from the signals API,
and each portfolio's trading controls from SQL Server."""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from optim import Dataset, Row
from optim.schema import Id

SQL = Path(__file__).parent / "sql"

type Action = Literal["no_buy", "no_sell", "hold", "must_sell"]


class Signal(Row):
    security_id: Id
    expected_return: float
    """Expected annual return on market value, as a decimal."""


class Control(Row):
    """A mandatory restriction on one portfolio's trading."""

    portfolio_id: Id
    control_id: Id
    security_id: Id | None
    """The security it controls; null: every security the portfolio holds or may buy."""
    action: Action
    """`no_buy`: may not be bought, as after a wash sale. `no_sell`: may not be sold at the model's discretion. `hold`:
    neither. `must_sell`: the whole holding is sold, and none is bought back."""
    reason: str
    """Why, as the source records it, such as `wash_sale` or `client_restriction`; reasons quote it."""


SIGNALS = Dataset("signals", Signal, source="rest", query="/v1/expected-returns", key=("security_id",), cache=timedelta(minutes=5))
CONTROLS = Dataset("controls", Control, source="mssql", query=SQL / "controls.sql", key=("portfolio_id", "control_id"))
