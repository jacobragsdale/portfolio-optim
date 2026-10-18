"""Command line. Choose the environment file explicitly:

uv run --env-file .env optim optimize --strategy strategies/core.json --portfolios A1 A2
uv run --env-file .env optim optimize --strategy strategies/core.json --portfolios @portfolios.txt
uv run --env-file .env optim model --strategy strategies/track-benchmark.json --benchmark IG-5Y
uv run --env-file .env optim transition --strategy strategies/track-benchmark.json --portfolio A1 --tax-budgets 0 25000 100000
uv run --env-file .env optim warm
uv run --env-file .env optim warm --datasets benchmarks
uv run --env-file .env optim clear --datasets securities universe
uv run optim replay snapshots/RUN_ID --strategy strategies/track-benchmark.json --workers 4
uv run optim schema > strategy.schema.json
"""

import argparse
import json
import logging
import os
import signal
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from optim import logs
from optim.context import open_cache, open_context
from optim.datasets import all_datasets
from optim.jobs import Run
from optim.jobs.model_portfolio import ModelPortfolioRequest, model_portfolio
from optim.jobs.optimize import OptimizeRequest, optimize
from optim.jobs.replay import ReplayRequest, replay
from optim.jobs.transition import TransitionRequest, transition
from optim.outputs.snapshots import result_frames
from optim.settings import Settings
from optim.strategy import Strategy

log = logging.getLogger(__name__)
MARKET_TIME = ZoneInfo("America/New_York")


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when every portfolio solved cleanly. `OPTIM_WORKERS` sets how many solver processes a run uses. An
    error is logged once, with its root cause, and exits 1."""
    args = _parser().parse_args(argv)
    if args.command == "schema":
        sys.stdout.write(json.dumps(Strategy.model_json_schema(), indent=2) + "\n")
        return 0
    signal.signal(signal.SIGTERM, _stopped)
    try:
        logs.setup()
        with logs.timed(log, f"finished {args.command}", level=logging.INFO) as fields:
            fields["exit_code"] = code = _run(args)
    except Exception:
        log.exception("%s failed", args.command, extra={"cli": logs.brief(vars(args))})
        return 1
    return code


def _stopped(signum: int, _frame: object) -> None:
    """Kubernetes stops a Job with SIGTERM, on a deadline, eviction or deletion: log it, then die by it as usual."""
    log.error("stopped by %s before finishing", signal.Signals(signum).name)
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _run(args: argparse.Namespace) -> int:
    if args.command == "replay":  # reads a snapshot only: no settings, connections or cache
        log.info("starting replay", extra={"cli": logs.brief(vars(args))})
        strategy = Strategy.model_validate_json(args.strategy.read_bytes())
        return _report(replay(ReplayRequest(strategy=strategy, snapshot=args.snapshot, portfolios=args.portfolios), workers=args.workers))
    settings = Settings()
    log.info("starting %s", args.command, extra={"cli": logs.brief(vars(args)), "settings": settings.model_dump(mode="json")})
    if args.command == "clear":
        return _clear(settings, args.datasets)
    as_of: date = args.as_of or datetime.now(MARKET_TIME).date()
    if args.command == "warm":
        return _warm(settings, as_of, args.datasets)
    strategy = Strategy.model_validate_json(args.strategy.read_bytes())

    with open_context(settings, cache=not args.no_cache) as ctx:
        match args.command:
            case "optimize":
                run = optimize(ctx, OptimizeRequest(strategy=strategy, portfolios=args.portfolios, as_of=as_of))
            case "model":
                run = model_portfolio(ctx, ModelPortfolioRequest(strategy=strategy, benchmark_id=args.benchmark, notional=args.notional, as_of=as_of))
            case _:
                run = transition(ctx, TransitionRequest(strategy=strategy, portfolio_id=args.portfolio, tax_budgets=args.tax_budgets, as_of=as_of))
    return _report(run)


def _report(run: Run) -> int:
    sys.stdout.write(f"run {run.run_id}\n{result_frames(run.results)['summary'].to_string(index=False)}\n")
    return 0 if run.ok else 1


def _warm(settings: Settings, as_of: date, names: Sequence[str] | None) -> int:
    """Fill the cache for the business date: every dataset with a warm hook, or only the named ones, say after a data
    correction. Exit 1 if any load failed."""
    if settings.redis_url is None:
        msg = "warm needs OPTIM_REDIS_URL: without Redis there is no cache to fill"
        raise ValueError(msg)
    with open_context(settings) as ctx:
        return 0 if ctx.loader.warm([d for d in all_datasets() if not names or d.name in names], as_of) else 1


def _clear(settings: Settings, names: Sequence[str]) -> int:
    """Delete every cached entry of the named datasets, whatever their params, say after a data correction. Until they
    are filled again, runs read them live, or fail where the policy forbids that."""
    if settings.redis_url is None:
        msg = "clear needs OPTIM_REDIS_URL: without Redis there is no cache to clear"
        raise ValueError(msg)
    with open_cache(settings.redis_url) as cache:
        for name in names:
            log.info("cleared %d entries of %s", cache.clear(name), name)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the strategy JSON schema")

    dated = argparse.ArgumentParser(add_help=False)
    dated.add_argument("--as-of", type=date.fromisoformat, help="business date (default: today in New York)")
    warm_command = commands.add_parser("warm", parents=[dated], help="refresh cached datasets for the business date, before the day's first run")
    warmable = sorted(d.name for d in all_datasets() if d.cache and d.cache.warm)
    warm_command.add_argument("--datasets", nargs="+", choices=warmable, help="warm only these (default: every dataset with a warm hook)")
    clear_command = commands.add_parser("clear", help="delete every cached entry of some datasets")
    cached = sorted(d.name for d in all_datasets() if d.cache)
    clear_command.add_argument("--datasets", nargs="+", choices=cached, required=True)

    run = argparse.ArgumentParser(add_help=False, parents=[dated])
    run.add_argument("--strategy", type=Path, required=True, help="strategy JSON file")
    run.add_argument("--no-cache", action="store_true", help="read every dataset live")

    optimize_command = commands.add_parser("optimize", parents=[run], help="rebalance portfolios")
    optimize_command.add_argument("--portfolios", nargs="+", required=True, help="portfolio ids, or @file with one per line")

    model_command = commands.add_parser("model", parents=[run], help="build a model portfolio from cash")
    model_command.add_argument("--benchmark", required=True)
    model_command.add_argument("--notional", type=float, default=100_000_000.0)

    transition_command = commands.add_parser("transition", parents=[run], help="tax cost vs. tracking for one portfolio")
    transition_command.add_argument("--portfolio", required=True)
    transition_command.add_argument("--tax-budgets", type=float, nargs="+", required=True, help="dollars of tax allowed, one solve each")

    replay_command = commands.add_parser("replay", help="re-solve a saved run with another strategy; reads only the snapshot")
    replay_command.add_argument("snapshot", type=Path, help="the run's snapshot directory")
    replay_command.add_argument("--strategy", type=Path, required=True, help="strategy JSON file")
    replay_command.add_argument("--portfolios", nargs="+", help="only these portfolios (default: every portfolio in the run)")
    replay_command.add_argument("--workers", type=int, default=1, help="solver processes (default: 1)")
    return parser
