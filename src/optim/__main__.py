"""Command line. Choose the environment file explicitly:

uv run --env-file .env optim optimize --strategy strategies/core-muni.json --accounts A1 A2
uv run --env-file .env optim optimize --strategy strategies/core-muni.json --accounts @accounts.txt --submit
uv run --env-file .env optim model --strategy strategies/track-benchmark.json --benchmark MUNI-5Y
uv run --env-file .env optim transition --strategy strategies/track-benchmark.json --account A1 --tax-budgets 0 25000 100000
uv run optim schema > strategy.schema.json
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import redis

from optim import jobs
from optim.cache import Cache, MemoryCache, RedisCache
from optim.data import Loader
from optim.lp import Solved
from optim.outputs import result_frames
from optim.run import Strategy
from optim.settings import Settings
from optim.sources import QUERY_TIMEOUT_S, open_sources

log = logging.getLogger("optim")
MARKET_TIME = ZoneInfo("America/New_York")


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when every portfolio solved cleanly."""
    args = _parser().parse_args(argv)
    if args.command == "schema":
        sys.stdout.write(json.dumps(Strategy.model_json_schema(), indent=2) + "\n")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    log.info("starting %s", args.command, extra={"cli": vars(args), "settings": settings.model_dump(mode="json")})
    strategy = Strategy.model_validate_json(args.strategy.read_bytes())
    as_of: date = args.as_of or datetime.now(MARKET_TIME).date()

    with ExitStack() as stack:
        loader = Loader(stack.enter_context(open_sources(settings)), cache=None if args.no_cache else _cache(settings))
        match args.command:
            case "optimize":
                oms = stack.enter_context(httpx.Client(base_url=str(settings.oms_url), timeout=QUERY_TIMEOUT_S)) if args.submit else None
                results = jobs.optimize(loader, args.accounts, strategy, as_of=as_of, workers=args.workers, snapshots=settings.snapshot_dir, oms=oms)
            case "model":
                results = jobs.model_portfolio(loader, strategy, benchmark_id=args.benchmark, notional=args.notional, as_of=as_of, snapshots=settings.snapshot_dir)
            case _:
                results = jobs.transition(loader, args.account, strategy, tax_budgets=args.tax_budgets, as_of=as_of, workers=args.workers, snapshots=settings.snapshot_dir)
    sys.stdout.write(result_frames(results)["summary"].to_string(index=False) + "\n")
    return 0 if all(isinstance(r, Solved) and r.ok for r in results) else 1


def _cache(settings: Settings) -> Cache:
    if settings.redis_url is None:
        return MemoryCache()
    return RedisCache(redis.Redis.from_url(str(settings.redis_url), socket_timeout=5))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the strategy JSON schema")

    run = argparse.ArgumentParser(add_help=False)
    run.add_argument("--strategy", type=Path, required=True, help="strategy JSON file")
    run.add_argument("--as-of", type=date.fromisoformat, help="business date (default: today in New York)")
    run.add_argument("--workers", type=int, default=os.process_cpu_count() or 1, help="solver processes")
    run.add_argument("--no-cache", action="store_true", help="read every dataset live")

    optimize = commands.add_parser("optimize", parents=[run], help="rebalance accounts")
    optimize.add_argument("--accounts", nargs="+", required=True, help="account ids, or @file with one per line")
    optimize.add_argument("--submit", action="store_true", help="send orders to the OMS (default: dry run)")

    model = commands.add_parser("model", parents=[run], help="build a model portfolio from cash")
    model.add_argument("--benchmark", required=True)
    model.add_argument("--notional", type=float, default=100_000_000)

    transition = commands.add_parser("transition", parents=[run], help="tax cost vs. tracking for one account")
    transition.add_argument("--account", required=True)
    transition.add_argument("--tax-budgets", type=float, nargs="+", required=True, help="dollars of tax allowed, one solve each")
    return parser


if __name__ == "__main__":
    sys.exit(main())
