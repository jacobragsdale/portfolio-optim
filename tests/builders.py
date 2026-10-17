"""Test data. A `FakeSource` serves raw records by query name, so tests run the real loading, validation and assembly
code. Builders give every record valid defaults, so a test states only the fields it is about."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import numpy as np

from optim.cache import Cache
from optim.context import Context
from optim.data.loader import Loader
from optim.datasets.accounts import ACCOUNTS, LOTS
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS
from optim.inputs import Inputs, load_market, load_portfolios
from optim.sources import Columns, Param, columns_from_records
from optim.strategy import Strategy

AS_OF = date(2026, 10, 16)
STRATEGIES = Path(__file__).parents[1] / "strategies"

type Record = dict[str, object]


@dataclass
class FakeSource:
    tables: Mapping[str, Sequence[Record]]
    calls: list[str] = field(default_factory=list)

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        self.calls.append(query)
        records = self.tables[query]
        if isinstance(accounts := params.get("accounts"), tuple):
            records = [r for r in records if r["account_id"] in accounts]
        return columns_from_records(records, source=query)


def cusip(i: int) -> str:
    return f"B{i:07d}0"


def security(i: int, **fields: object) -> Record:
    defaults: Record = {
        "cusip": cusip(i),
        "issuer": f"ISSUER{i}",
        "sector": "GO",
        "state": "NY",
        "rating": "AA",
        "tax_status": "muni",
        "maturity": date(2033, 6, 1),
        "price": 100.0,
        "duration": 5.0,
        "cost_bps": 10.0,
        "min_piece": 5000,
        "increment": 5000,
    }
    return defaults | fields


def account(account_id: str = "A1", **fields: object) -> Record:
    defaults: Record = {"account_id": account_id, "benchmark_id": "BM", "cash": 0.0, "state": "NY", "federal_rate": 0.37, "state_rate": 0.10, "st_gain_rate": 0.47, "lt_gain_rate": 0.30}
    return defaults | fields


def lot(i: int, lot_id: str, account_id: str = "A1", **fields: object) -> Record:
    defaults: Record = {"account_id": account_id, "lot_id": lot_id, "cusip": cusip(i), "par": 100_000, "cost_price": 100.0, "acquired": date(2024, 1, 2)}
    return defaults | fields


def tables(
    securities: Sequence[Record], *, accounts: Sequence[Record] = (), lots: Sequence[Record] = (), expected_return: float = 0.03, benchmark: Sequence[str] | None = None
) -> dict[str, Sequence[Record]]:
    """Every security is offered and scored; the benchmark is equal-weighted over `benchmark` (default: all)."""
    members = benchmark if benchmark is not None else [str(s["cusip"]) for s in securities]
    return {
        SECURITIES.query: securities,
        UNIVERSE.query: [{"cusip": s["cusip"], "offer_par": 5_000_000} for s in securities],
        SIGNALS.query: [{"cusip": s["cusip"], "expected_return": expected_return} for s in securities],
        BENCHMARKS.query: [{"benchmark_id": "BM", "cusip": c, "weight": 1 / len(members)} for c in members],
        ACCOUNTS.query: accounts,
        LOTS.query: lots,
    }


def loader(data: Mapping[str, Sequence[Record]], cache: Cache | None = None) -> tuple[Loader, FakeSource]:
    source = FakeSource(data)
    return Loader({"oracle": source, "mssql": source, "signals": source}, cache), source


def inputs(data: Mapping[str, Sequence[Record]], account_id: str = "A1") -> Inputs:
    """One account's inputs, through the real loading and assembly path."""
    load, _ = loader(data)
    [portfolio] = load_portfolios(load, [account_id])
    return load_market(load, AS_OF).inputs_for(portfolio)


def context(data: Mapping[str, Sequence[Record]], snapshot_dir: Path, *, oms: Callable[[httpx.Request], httpx.Response] | None = None) -> Context:
    """A `Context` over fake sources, with an OMS that accepts every request unless `oms` says otherwise."""
    load, _ = loader(data)
    transport = httpx.MockTransport(oms or (lambda _: httpx.Response(200)))
    return Context(load, httpx.Client(base_url="https://oms.example.com", transport=transport), snapshot_dir)


def strategy(name: str) -> Strategy:
    return Strategy.model_validate_json((STRATEGIES / f"{name}.json").read_bytes())


def synthetic_book(accounts: int = 3, securities: int = 80, seed: int = 7) -> dict[str, Sequence[Record]]:
    """A small, varied market and diversified accounts: enough for every example strategy to be feasible."""
    rng = np.random.default_rng(seed)
    states = ["NY", "NY", "CA", "NJ", None]
    bonds = [
        security(
            i,
            issuer=f"ISSUER{i // 2}",
            sector=["GO", "REV", "UTIL"][i % 3],
            state=states[i % 5],
            tax_status="taxable" if states[i % 5] is None else "muni",
            rating=["AAA", "AA", "AA-", "A", "BBB"][i % 5],
            maturity=date(2027 + i % 20, 6, 1),
            price=float(rng.uniform(95, 105)),
            duration=0.9 * (1 + i % 20),
        )
        for i in range(securities)
    ]
    holders = [account(f"A{a}", cash=40_000.0) for a in range(accounts)]
    lots = [
        lot(i, f"L{a}-{i}", f"A{a}", par=50_000, cost_price=float(rng.uniform(90, 110)), acquired=date(2025 - i % 3, 3, 1))
        for a in range(accounts)
        for i in rng.choice(securities, size=25, replace=False).tolist()
    ]
    return tables(bonds, accounts=holders, lots=lots, expected_return=0.03, benchmark=[cusip(i) for i in range(0, securities, 2)]) | {
        SIGNALS.query: [{"cusip": b["cusip"], "expected_return": float(rng.uniform(0.02, 0.05))} for b in bonds]
    }
