"""Bonds, from Oracle: reference data and analytics for everything we can hold or buy, and the universe on offer
today. Both may be up to 15 minutes old."""

from datetime import date, timedelta
from typing import Annotated, Literal

import pandas as pd
from pydantic import Field

from optim.data.loader import CachePolicy, Dataset
from optim.schema import Cusip, Par, Price, Rating, Row


class Security(Row):
    cusip: Cusip
    issuer: str
    sector: str
    rating: Rating
    tax_status: Literal["taxable", "state_exempt"]  # state_exempt: no state tax on interest, as for Treasuries
    maturity: date
    price: Price  # dirty: what a trade pays, accrued interest included
    accrued: Annotated[float, Field(ge=0)]  # accrued interest in `price`; price less accrued is the clean price, which gains are measured from
    duration: Annotated[float, Field(ge=0)]  # modified, years
    cost_bps: Annotated[float, Field(ge=0)]  # estimated one-way trading cost
    min_piece: Par  # smallest trade
    increment: Par  # trades are whole multiples of it
    min_remaining: Par  # smallest amount a partial sale may leave in a lot


class Offering(Row):
    cusip: Cusip
    offer_par: Par  # par we can buy today


def _accrued_below_price(frame: pd.DataFrame) -> None:
    if not (bad := frame[frame["accrued"] >= frame["price"]]).empty:
        msg = f"accrued interest must be below the dirty price, e.g. {bad[['cusip', 'price', 'accrued']].head(3).to_dict('records')}"
        raise ValueError(msg)


SECURITIES = Dataset("securities", Security, source="oracle", query="datasets/sql/securities.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)), check=_accrued_below_price)
UNIVERSE = Dataset("universe", Offering, source="oracle", query="datasets/sql/universe.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)))
