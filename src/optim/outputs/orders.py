"""Orders, built from the results that are safe to trade and validated before they leave."""

from collections.abc import Iterable
from typing import Literal

import pandas as pd
from pydantic import Field

from optim.optimizer.results import Result, Solved
from optim.schema import Cusip, Par, Strict


class Order(Strict):
    client_order_id: str  # deterministic, so the OMS deduplicates a resubmitted run
    account_id: str
    cusip: Cusip
    side: Literal["buy", "sell"]
    par: Par
    lots: dict[str, Par] = Field(default_factory=dict)  # sells: par relieved per tax lot (specific identification)


def orders_for(run_id: str, results: Iterable[Result]) -> list[Order]:
    """One order per account, bond and side, from the results that rounding left intact."""
    tradable = [r.trades for r in results if isinstance(r, Solved) and r.ok]
    if not tradable:
        return []
    return [
        Order.model_validate(
            {
                "client_order_id": f"{run_id}:{account_id}:{cusip}:{side}",
                "account_id": account_id,
                "cusip": cusip,
                "side": side,
                "par": int(group["par"].sum()),
                "lots": dict(zip(group["lot_id"].tolist(), group["par"].tolist(), strict=True)) if side == "sell" else {},
            }
        )
        for (account_id, cusip, side), group in pd.concat(tradable, ignore_index=True).groupby(["account_id", "cusip", "side"])
    ]
