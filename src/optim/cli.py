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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from optim.context import open_context
from optim.jobs.model_portfolio import ModelPortfolioRequest, model_portfolio
from optim.jobs.optimize import OptimizeRequest, optimize
from optim.jobs.transition import TransitionRequest, transition
from optim.optimizer.results import Solved
from optim.outputs.snapshots import result_frames
from optim.settings import Settings
from optim.strategy import Strategy

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

    with open_context(settings, cache=not args.no_cache) as ctx:
        match args.command:
            case "optimize":
                request = OptimizeRequest(strategy=strategy, accounts=tuple(args.accounts), as_of=as_of, workers=args.workers, submit=args.submit)
                results = optimize(ctx, request)
            case "model":
                results = model_portfolio(ctx, ModelPortfolioRequest(strategy=strategy, benchmark_id=args.benchmark, notional=args.notional, as_of=as_of))
            case _:
                request = TransitionRequest(strategy=strategy, account_id=args.account, tax_budgets=tuple(args.tax_budgets), as_of=as_of, workers=args.workers)
                results = transition(ctx, request)
    sys.stdout.write(result_frames(results)["summary"].to_string(index=False) + "\n")
    return 0 if all(isinstance(r, Solved) and r.ok for r in results) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the strategy JSON schema")

    run = argparse.ArgumentParser(add_help=False)
    run.add_argument("--strategy", type=Path, required=True, help="strategy JSON file")
    run.add_argument("--as-of", type=date.fromisoformat, help="business date (default: today in New York)")
    run.add_argument("--workers", type=int, default=os.process_cpu_count() or 1, help="solver processes")
    run.add_argument("--no-cache", action="store_true", help="read every dataset live")

    optimize_command = commands.add_parser("optimize", parents=[run], help="rebalance accounts")
    optimize_command.add_argument("--accounts", nargs="+", required=True, help="account ids, or @file with one per line")
    optimize_command.add_argument("--submit", action="store_true", help="send orders to the OMS (default: dry run)")

    model_command = commands.add_parser("model", parents=[run], help="build a model portfolio from cash")
    model_command.add_argument("--benchmark", required=True)
    model_command.add_argument("--notional", type=float, default=100_000_000.0)

    transition_command = commands.add_parser("transition", parents=[run], help="tax cost vs. tracking for one account")
    transition_command.add_argument("--account", required=True)
    transition_command.add_argument("--tax-budgets", type=float, nargs="+", required=True, help="dollars of tax allowed, one solve each")
    return parser
