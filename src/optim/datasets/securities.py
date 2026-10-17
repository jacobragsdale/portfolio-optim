"""Bonds, from Oracle: reference data and analytics for everything we can hold or buy, and the universe on offer
today. Both may be up to 15 minutes old."""

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import Field

from optim.data.loader import CachePolicy, Dataset
from optim.schema import Cusip, Date, Par, Price, Rating, Row, State


class Security(Row):
    cusip: Cusip
    issuer: str
    sector: str
    state: State | None  # munis only
    rating: Rating
    tax_status: Literal["taxable", "muni"]
    maturity: Date
    price: Price  # dirty
    duration: Annotated[float, Field(ge=0)]  # modified, years
    cost_bps: Annotated[float, Field(ge=0)]  # estimated one-way trading cost
    min_piece: Par  # smallest trade
    increment: Par  # trades are whole multiples of it
    min_remaining: Par  # smallest amount a partial sale may leave in a lot


class Offering(Row):
    cusip: Cusip
    offer_par: Par  # par we can buy today


SECURITIES = Dataset("securities", Security, source="oracle", query="datasets/sql/securities.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)))
UNIVERSE = Dataset("universe", Offering, source="oracle", query="datasets/sql/universe.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)))
