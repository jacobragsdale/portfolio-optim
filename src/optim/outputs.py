"""What leaves the system: orders to the OMS, and a snapshot of every run so any result can be explained and replayed."""

import logging
from collections.abc import Iterable, Mapping, Sequence
from itertools import batched
from pathlib import Path
from typing import Literal

import httpx
import pandas as pd
import pydantic_core
from pydantic import Field, TypeAdapter

from optim.data import Strict
from optim.inputs import Cusip, Market, Par, Portfolio
from optim.lp import Result, Solved, Unsolved

log = logging.getLogger(__name__)


class Order(Strict):
    """An outbound order, validated before it is sent."""

    client_order_id: str  # deterministic, so the OMS deduplicates a resubmitted run
    account_id: str
    cusip: Cusip
    side: Literal["buy", "sell"]
    par: Par
    lots: dict[str, Par] = Field(default_factory=dict)  # sells: par relieved per tax lot (specific identification)


_ORDERS = TypeAdapter(list[Order])


def orders_for(run_id: str, results: Iterable[Result]) -> list[Order]:
    """One order per account, bond and side, from the results that are safe to trade."""
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


def submit_orders(oms: httpx.Client, orders: Sequence[Order], *, batch_size: int = 500) -> None:
    """POST orders in batches. A failed batch raises; batches already sent stay sent, and resubmitting the whole run is
    safe because the OMS deduplicates on `client_order_id`."""
    for batch in batched(orders, batch_size, strict=False):
        response = oms.post("/orders", content=_ORDERS.dump_json(list(batch)), headers={"content-type": "application/json"})
        response.raise_for_status()
        log.info("submitted %d orders", len(batch), extra={"first_order": batch[0].client_order_id})


def save_snapshot(root: Path, run_id: str, *, meta: Mapping[str, object], frames: Mapping[str, pd.DataFrame]) -> Path:
    """Write `run.json` and one Parquet file per frame to `root/run_id/`. The directory appears only once complete."""
    partial, path = root / f".{run_id}.partial", root / run_id
    partial.mkdir(parents=True)
    (partial / "run.json").write_bytes(pydantic_core.to_json(meta, indent=2))
    for name, frame in frames.items():
        frame.to_parquet(partial / f"{name}.parquet", index=False)
    partial.rename(path)
    log.info("saved snapshot %s", path, extra={"run_id": run_id})
    return path


def input_frames(market: Market, portfolios: Sequence[Portfolio]) -> dict[str, pd.DataFrame]:
    """Everything the solves read, so a run can be replayed exactly."""
    # ponytail: writes the whole market every run; store it once per content hash if snapshots grow too fast
    return {
        "securities": market.securities,
        "targets": pd.concat(market.targets, names=["benchmark_id"]).reset_index(),
        "accounts": pd.DataFrame([p.account.model_dump() for p in portfolios]),
        "lots": pd.concat([p.lots for p in portfolios], ignore_index=True),
    }


def result_frames(results: Sequence[Result]) -> dict[str, pd.DataFrame]:
    solved = [r for r in results if isinstance(r, Solved)]
    return {
        "trades": _concat([r.trades.assign(strategy=r.strategy) for r in solved]),
        "positions": _concat([r.positions.assign(account_id=r.account_id, strategy=r.strategy) for r in solved]),
        "summary": pd.DataFrame([_summary_row(r) for r in results]),
    }


def _summary_row(result: Result) -> dict[str, object]:
    match result:
        case Solved():
            status = "ok" if result.ok else f"rounding broke {sorted(result.violations)}"
            return {"account_id": result.account_id, "strategy": result.strategy, "status": status, **result.summary, "seconds": result.seconds}
        case Unsolved():
            return {"account_id": result.account_id, "strategy": result.strategy, "status": result.status}


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
