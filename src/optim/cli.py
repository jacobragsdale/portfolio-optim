"""The command line. Settings come from `OPTIM_*` environment variables; choose the environment file explicitly:

uv run --env-file .env optim run ladder --params params/ladder/default.json --portfolios P1 P2
uv run --env-file .env optim run ladder --params params/ladder/default.json --portfolios @portfolios.txt --as-of 2026-10-16
uv run --env-file .env optim run ladder --params params/ladder/default.json --data tests/ladder/fixture --as-of 2026-10-16
uv run --env-file .env optim replay RUN_ID --params params/ladder/wider.json
uv run --env-file .env optim warm ladder
"""

import argparse
import logging
import os
import signal
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from optim import logs, models
from optim.context import open_context
from optim.contract import declared
from optim.run import Run, replay, run
from optim.settings import Settings
from optim.snapshots import result_frames
from optim.storage import Root

log = logging.getLogger(__name__)
MARKET_TIME = ZoneInfo("America/New_York")


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when every portfolio was accepted. An error is logged once, with its root cause, and exits 1."""
    args = _parser().parse_args(argv)
    signal.signal(signal.SIGTERM, _stopped)
    try:
        logs.setup()
        with logs.timed(log, f"finished {args.command}") as fields:
            fields["exit_code"] = code = _run(args)
    except Exception:
        log.exception("%s failed", args.command, extra={"cli": logs.brief(vars(args))})
        return 1
    return code


def _stopped(signum: int, _frame: object) -> None:
    """A scheduler stops a run with SIGTERM, on a deadline or a cancellation: log it, then die by it as usual."""
    log.error("stopped by %s before finishing", signal.Signals(signum).name)
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    workers = getattr(args, "workers", None) or settings.workers
    match args.command:
        case "run":
            model = models.load(args.model)
            with open_context(settings, cache=not args.no_cache) as ctx:
                as_of_date = args.as_of or _today()
                labels = dict(args.labels)
                ran = run(
                    ctx.loader, ctx.snapshot_root, model, args.params, as_of_date=as_of_date, portfolio_ids=args.portfolios, data_dir=args.data, workers=workers, labels=labels, orders=ctx.orders
                )
            return _report(ran)
        case "replay":  # reads a snapshot and writes one: no sources or cache
            root = Root.at(settings.snapshot_root)
            return _report(replay(_snapshot(args.run, root), root, params_file=args.params, portfolio_ids=args.portfolios, workers=workers, labels=dict(args.labels)))
        case "warm":
            if settings.cache_root is None:
                msg = "warm needs OPTIM_CACHE_ROOT: without it there is no cache to fill"
                raise ValueError(msg)
            datasets = {dataset.name: dataset for name in args.models for dataset in declared(models.load(name).data).values()}
            with open_context(settings) as ctx:
                return 0 if ctx.loader.warm(datasets.values(), args.as_of or _today()) else 1
        case _:
            msg = f"no command {args.command!r}: add a case for it here"
            raise ValueError(msg)


def _report(ran: Run) -> int:
    decisions = result_frames(ran.outcomes)["decisions"][["portfolio_id", "status", "trades", "solved", "objective", "stop", "seconds"]]
    sys.stdout.write(f"run {ran.run_id}\n{decisions.to_string(index=False)}\n")
    for outcome in ran.outcomes:
        for reason in outcome.reasons:
            sys.stdout.write(f"{outcome.portfolio_id}: {reason}\n")
    return 0 if ran.ok else 1


def _snapshot(run: str, root: Root) -> Root:
    """A run by its id, under the snapshot root, or by the path or URL of its snapshot."""
    return Root.at(run) if "/" in run or "\\" in run else root / run


def _today() -> date:
    return datetime.now(MARKET_TIME).date()


def _label(text: str) -> tuple[str, str]:
    key, equals, value = text.partition("=")
    if not (key and equals):
        msg = f"expected KEY=VALUE, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return key, value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)

    deciding = argparse.ArgumentParser(add_help=False)
    deciding.add_argument("--label", type=_label, action="append", default=[], dest="labels", metavar="KEY=VALUE", help="recorded in the run's run.json, e.g. purpose=production; repeat for more")
    deciding.add_argument("--workers", type=int, help="worker processes (default: OPTIM_WORKERS, or 1)")
    dated = argparse.ArgumentParser(add_help=False)
    dated.add_argument("--as-of", type=date.fromisoformat, help="the business date (default: today in New York)")

    run_command = commands.add_parser("run", parents=[deciding, dated], help="decide portfolios with a model, and save the run's snapshot")
    run_command.add_argument("model", help="a model's name in the optim.models entry points, or its module path")
    run_command.add_argument("--params", type=Path, required=True, help="the params JSON file")
    run_command.add_argument("--portfolios", nargs="+", help="portfolio ids, or @file with one per line (default, with --data: every portfolio in its holdings)")
    run_command.add_argument("--data", type=Path, help="read each dataset that has a file here, NAME.parquet or NAME.csv, instead of its source")
    run_command.add_argument("--no-cache", action="store_true", help="read every dataset live")

    replay_command = commands.add_parser("replay", parents=[deciding], help="decide a saved run again with today's code, and other params if given; reads only the snapshot")
    replay_command.add_argument("run", help="a run id under OPTIM_SNAPSHOT_ROOT, or a snapshot's path or URL")
    replay_command.add_argument("--params", type=Path, help="a params JSON file (default: the params the run used)")
    replay_command.add_argument("--portfolios", nargs="+", help="only these portfolios (default: every portfolio in the run)")

    warm_command = commands.add_parser("warm", parents=[dated], help="fill the cache with every cached dataset the models read, before the day's first run")
    warm_command.add_argument("models", nargs="+", help="model names or module paths")
    return parser
