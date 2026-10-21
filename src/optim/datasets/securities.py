"""Securities, from Oracle: reference data, prices and analytics for everything a portfolio can hold or buy, and the
eligibility lists that say what each sleeve may buy. Both may be up to 15 minutes old."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import pandas as pd
from pydantic import Field

from optim.data.dataset import CachePolicy, Dataset
from optim.schema import Currency, Cusip, Id, Isin, Par, Price, Quantity, Rating, Row

# TODO(port): the old system's numeric analytics, such as spread duration or yield, join `Security` and this list.
type Analytic = Literal["duration"]  # numeric columns a constraint or term can measure exposure in


class Security(Row):
    security_id: Id
    """The canonical id every dataset joins on. A CUSIP or ISIN is an identifier the security may also have."""
    cusip: Cusip | None
    isin: Isin | None
    asset_class: Literal["bond", "cash", "other"]
    """`cash`: a holding of it is the portfolio's cash, valued at its price and never traded. `other` covers what is
    neither, such as a corporate-action position."""
    currency: Currency
    issuer: str | None
    sector: str | None
    rating: Rating | None
    maturity: date | None
    duration: float | None
    """Modified duration, years."""
    price: Price
    """Clean price per `price_per` of quantity."""
    accrued: Decimal
    """Accrued interest per `price_per` of quantity; 0 when `price` already includes it."""
    price_per: Annotated[int, Field(gt=0)]
    """The quantity a price is quoted for: 100 for a bond quoted in percent of par, 1 for cash."""
    cost_bps: Annotated[float, Field(ge=0)]
    """Estimated one-way trading cost, basis points of value."""
    min_piece: Par | None
    """Smallest trade. The three denominations are all null for a security that does not trade in round lots, such as
    cash or a corporate-action position: it cannot be bought, and a holding of it can only be sold whole."""
    increment: Par | None
    """Trades above the minimum piece are whole multiples of it."""
    min_remaining: Par | None
    """Smallest holding a partial sale may leave."""


class Eligible(Row):
    """One security on one eligibility list. A sleeve buys only from its list; a list is not inventory, unless it says
    how much is available."""

    universe_id: Id
    security_id: Id
    available: Quantity | None
    """Quantity that can be bought, where the list is an inventory; null: no limit from inventory."""


def _denominations_together(frame: pd.DataFrame) -> None:
    given = frame[["min_piece", "increment", "min_remaining"]].notna()
    if (partly := given.any(axis=1) & ~given.all(axis=1)).any():
        msg = f"securities: min_piece, increment and min_remaining are all set or all null, e.g. {frame.loc[partly, 'security_id'].head(3).tolist()}"
        raise ValueError(msg)


SECURITIES = Dataset("securities", Security, source="oracle", query="datasets/sql/securities.sql", key=("security_id",), cache=CachePolicy(ttl=timedelta(minutes=15)), check=_denominations_together)
UNIVERSE = Dataset("universe", Eligible, source="oracle", query="datasets/sql/universe.sql", key=("universe_id", "security_id"), cache=CachePolicy(ttl=timedelta(minutes=15)))
