"""Bonds, from Oracle: reference data and analytics for everything we can hold or buy, and the universe on offer
today. Both may be up to 15 minutes old."""

from datetime import timedelta
from typing import Annotated

from pydantic import Field

from optim.data.loader import CachePolicy, Dataset
from optim.schema import Cusip, Par, Price, Rating, Row


class Security(Row):
    cusip: Cusip
    issuer: str
    sector: str
    rating: Rating
    price: Price
    """Price per 100 par: what a trade pays, and what gains are measured from."""
    cost_bps: Annotated[float, Field(ge=0)]
    """Estimated one-way trading cost, basis points."""
    min_piece: Par
    """Smallest trade, par."""
    increment: Par
    """Trades are whole multiples of it, par."""
    min_remaining: Par
    """Smallest amount a partial sale may leave in a lot, par."""


class Offering(Row):
    cusip: Cusip
    offer_par: Par
    """Par we can buy today."""


SECURITIES = Dataset("securities", Security, source="oracle", query="datasets/sql/securities.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)))
UNIVERSE = Dataset("universe", Offering, source="oracle", query="datasets/sql/universe.sql", key=("cusip",), cache=CachePolicy(ttl=timedelta(minutes=15)))
