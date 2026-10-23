"""The command line. Settings come from `OPTIM_*` environment variables; choose the environment file explicitly:

uv run --env-file .env optim run ladder --params params/ladder/default.json --portfolios P1 P2
uv run --env-file .env optim run ladder --params params/ladder/default.json --portfolios @portfolios.txt --as-of 2026-10-16
uv run --env-file .env optim run ladder --params params/ladder/default.json --data tests/ladder/fixture --as-of 2026-10-16
uv run --env-file .env optim replay RUN_ID --params params/ladder/wider.json
uv run --env-file .env optim sweep RUN_ID params/ladder/sweep --out sweep.csv
uv run --env-file .env optim regress ladder --snapshots //share/optim/snapshots --label purpose=production
uv run --env-file .env optim warm ladder
uv run optim describe ladder
uvx --index INDEX_URL optim new repo ladder --from cvxpy
uv run optim new model model-portfolio --from plain
uv run optim sync
"""

import argparse
import json
import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from optim import logs, models, new, research, sync
from optim.context import open_context
from optim.contract import declared
from optim.run import Run, open_snapshot, replay, run
from optim.settings import Settings
from optim.snapshots import result_frames
from optim.storage import Root

log = logging.getLogger(__name__)
MARKET_TIME = ZoneInfo("America/New_York")


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when every portfolio was accepted. An error is logged once, with its root cause, and exits 1."""
    args = _parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler  # each command's, set by `_command`
    signal.signal(signal.SIGTERM, _stopped)
    try:
        logs.setup()
        with logs.timed(log, f"finished {args.command}") as fields:
            fields["exit_code"] = code = handler(args)
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
    settings, model = Settings(), models.load(args.model)
    with open_context(settings, cache=not args.no_cache) as ctx:
        as_of_date, workers, labels = args.as_of or _today(), args.workers or settings.workers, dict(args.labels)
        ran = run(ctx.loader, ctx.snapshot_root, model, args.params, as_of_date=as_of_date, portfolio_ids=args.portfolios, data_dir=args.data, workers=workers, labels=labels, orders=ctx.orders)
    return _report(ran)


def _replay(args: argparse.Namespace) -> int:
    """Reads a snapshot and writes one: no sources or cache."""
    settings = Settings()
    ran = replay(_snapshot(args.run), Root.at(settings.snapshot_root), params_file=args.params, portfolio_ids=args.portfolios, workers=args.workers or settings.workers, labels=dict(args.labels))
    return _report(ran)


def _sweep(args: argparse.Namespace) -> int:
    """Reads a snapshot alone, and writes only the table."""
    if not (variants := sorted(args.variants.glob("*.json"))):
        msg = f"{args.variants} has no params files (*.json) to sweep"
        raise FileNotFoundError(msg)
    _write(research.sweep(open_snapshot(_snapshot(args.run)), variants, workers=args.workers or 1), args.out)
    return 0


def _regress(args: argparse.Namespace) -> int:
    changes = research.regress(models.load(args.model), Root.at(args.snapshots or Settings().snapshot_root), last=args.last, labels=dict(args.labels), workers=args.workers or 1)
    _write(changes, args.out)
    return 1 if len(changes) else 0


def _describe(args: argparse.Namespace) -> int:
    sys.stdout.write(json.dumps(research.describe(models.load(args.model)), indent=2) + "\n")
    return 0


def _new_repo(args: argparse.Namespace) -> int:
    index = args.index or os.environ.get("UV_INDEX")
    repo = new.new_repo(args.name, example=args.example, model=args.model, directory=args.dir, index=index, uv_sync=not args.no_sync)
    sys.stdout.write(f"created {repo}: commit it once you have read it\n")
    return 0


def _new_model(args: argparse.Namespace) -> int:
    sys.stdout.write(f"created {new.new_model(args.name, example=args.example, repo=Path.cwd(), uv_sync=not args.no_sync)}\n")
    return 0


def _sync(_args: argparse.Namespace) -> int:
    for path in sync.sync(Path.cwd()):
        sys.stdout.write(f"wrote {path}\n")
    return 0


def _warm(args: argparse.Namespace) -> int:
    settings = Settings()
    if settings.cache_root is None:
        msg = "warm needs OPTIM_CACHE_ROOT: without it there is no cache to fill"
        raise ValueError(msg)
    datasets = {dataset.name: dataset for name in args.models for dataset in declared(models.load(name).data).values()}
    with open_context(settings) as ctx:
        return 0 if ctx.loader.warm(datasets.values(), args.as_of or _today()) else 1


def _write(table: pd.DataFrame, out: Path | None) -> None:
    """A table as CSV on stdout, or to `out` as Parquet or CSV by its suffix."""
    if out is None:
        sys.stdout.write(table.to_csv(index=False))
    elif out.suffix == ".parquet":
        table.to_parquet(out, index=False)
    else:
        table.to_csv(out, index=False)


def _report(ran: Run) -> int:
    decisions = result_frames(ran.outcomes)["decisions"][["portfolio_id", "status", "trades", "solved", "objective", "stop", "seconds"]]
    sys.stdout.write(f"run {ran.run_id}\n{decisions.to_string(index=False)}\n")
    for outcome in ran.outcomes:
        for reason in outcome.reasons:
            sys.stdout.write(f"{outcome.portfolio_id}: {reason}\n")
    return 0 if ran.ok else 1


def _snapshot(run: str) -> Root:
    """A run by the path or URL of its snapshot, or by its id, under `OPTIM_SNAPSHOT_ROOT`."""
    return Root.at(run) if "/" in run or "\\" in run else Root.at(Settings().snapshot_root) / run


def _today() -> date:
    return datetime.now(MARKET_TIME).date()


def _label(text: str) -> tuple[str, str]:
    key, equals, value = text.partition("=")
    if not (key and equals):
        msg = f"expected KEY=VALUE, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return key, value


def _command(
    commands: argparse._SubParsersAction[argparse.ArgumentParser], name: str, handler: Callable[[argparse.Namespace], int], help_: str, *parents: argparse.ArgumentParser
) -> argparse.ArgumentParser:
    """A command's parser, which runs `handler` with its arguments."""
    parser = commands.add_parser(name, parents=list(parents), help=help_)
    parser.set_defaults(handler=handler)
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)

    deciding = argparse.ArgumentParser(add_help=False)
    deciding.add_argument("--label", type=_label, action="append", default=[], dest="labels", metavar="KEY=VALUE", help="recorded in the run's run.json, e.g. purpose=production; repeat for more")
    deciding.add_argument("--workers", type=int, help="worker processes (default: OPTIM_WORKERS, or 1)")
    tabled = argparse.ArgumentParser(add_help=False)
    tabled.add_argument("--out", type=Path, help="write the table to this .csv or .parquet file (default: CSV on stdout)")
    tabled.add_argument("--workers", type=int, help="worker processes (default: 1)")
    dated = argparse.ArgumentParser(add_help=False)
    dated.add_argument("--as-of", type=date.fromisoformat, help="the business date (default: today in New York)")

    run_command = _command(commands, "run", _run, "decide portfolios with a model, and save the run's snapshot", deciding, dated)
    run_command.add_argument("model", help="a model's name in the optim.models entry points, or its module path")
    run_command.add_argument("--params", type=Path, required=True, help="the params JSON file")
    run_command.add_argument("--portfolios", nargs="+", help="portfolio ids, or @file with one per line (default, with --data: every portfolio in its holdings)")
    run_command.add_argument("--data", type=Path, help="read each dataset that has a file here, NAME.parquet or NAME.csv, instead of its source")
    run_command.add_argument("--no-cache", action="store_true", help="read every dataset live")

    replay_command = _command(commands, "replay", _replay, "decide a saved run again with today's code, and other params if given; reads only the snapshot", deciding)
    replay_command.add_argument("run", help="a run id under OPTIM_SNAPSHOT_ROOT, or a snapshot's path or URL")
    replay_command.add_argument("--params", type=Path, help="a params JSON file (default: the params the run used)")
    replay_command.add_argument("--portfolios", nargs="+", help="only these portfolios (default: every portfolio in the run)")

    sweep_command = _command(commands, "sweep", _sweep, "decide a saved run once per params file in a directory, into one comparison table; reads only the snapshot", tabled)
    sweep_command.add_argument("run", help="a run id under OPTIM_SNAPSHOT_ROOT, or a snapshot's path or URL")
    sweep_command.add_argument("variants", type=Path, help="a directory of params JSON files, one per variant")

    regress_command = _command(commands, "regress", _regress, "replay a model's recent runs with today's code, and list every trade that changed; exits 1 if any did", tabled)
    regress_command.add_argument("model", help="a model's name in the optim.models entry points, or its module path")
    regress_command.add_argument("--snapshots", help="the snapshot root to read, such as production's (default: OPTIM_SNAPSHOT_ROOT)")
    regress_command.add_argument("--last", type=int, default=20, help="how many of the most recent runs (default: 20)")
    regress_command.add_argument("--label", type=_label, action="append", default=[], dest="labels", metavar="KEY=VALUE", help="only runs labelled so, e.g. purpose=production")

    describe_command = _command(commands, "describe", _describe, "print a model's params schema, datasets and metrics as JSON")
    describe_command.add_argument("model", help="a model's name in the optim.models entry points, or its module path")

    new_command = commands.add_parser("new", help="make a model repo, or add a model to this one, from an example")
    new_kind = new_command.add_subparsers(dest="what", required=True)
    example = argparse.ArgumentParser(add_help=False)
    example.add_argument("--from", dest="example", choices=new.EXAMPLES, default="cvxpy", help="the example to copy: cvxpy, which solves through optim.cvxpy, or plain (default: cvxpy)")
    example.add_argument("--no-sync", action="store_true", help="leave out `uv sync`")
    repo_command = _command(new_kind, "repo", _new_repo, "make a model repo with one model, run git init and uv sync", example)
    repo_command.add_argument("name", help="the repo, and its package")
    repo_command.add_argument("--model", help="its first model's name (default: the repo's)")
    repo_command.add_argument("--dir", type=Path, default=Path.cwd(), help="where to make it (default: here)")
    repo_command.add_argument("--index", help="the internal index optim is published to, written into its pyproject (default: UV_INDEX)")
    model_command = _command(new_kind, "model", _new_model, "add a model to the repo this runs in, with its entry point", example)
    model_command.add_argument("name", help="the model's name, which names its module, params and tests")
    _command(commands, "sync", _sync, "write optim's contract into AGENTS.md and copy its skills, for the installed optim")

    warm_command = _command(commands, "warm", _warm, "fill the cache with every cached dataset the models read, before the day's first run", dated)
    warm_command.add_argument("models", nargs="+", help="model names or module paths")
    return parser
