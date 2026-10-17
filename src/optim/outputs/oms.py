"""The order management system's REST API."""

import logging
from collections.abc import Sequence
from itertools import batched

import httpx
from pydantic import TypeAdapter

from optim.outputs.orders import Order

log = logging.getLogger(__name__)

_ORDERS = TypeAdapter(list[Order])


def submit_orders(oms: httpx.Client, orders: Sequence[Order], *, batch_size: int = 500) -> None:
    """POST orders in batches. A failed batch raises; batches already sent stay sent, and resubmitting the whole run is
    safe because the OMS deduplicates on `client_order_id`."""
    for batch in batched(orders, batch_size, strict=False):
        response = oms.post("/orders", content=_ORDERS.dump_json(list(batch)), headers={"content-type": "application/json"})
        response.raise_for_status()
        log.info("submitted %d orders", len(batch), extra={"first_order": batch[0].client_order_id})
