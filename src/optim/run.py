"""Running a model. `run` validates the params, loads the model's data, decides every portfolio and saves the snapshot;
`replay` decides a saved run again, with the code you have now and, if given, other params. Both decide through
`decide_all`, which calls the model's hooks: `prepare` once in this process, then `prepare_portfolio`, `decide` and
`check` for each portfolio, in worker processes."""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike

import optim
from optim import engine, logs, models
from optim.contract import Datasets, Decision, Params, declared
from optim.data.dataset import Dataset
from optim.data.loader import Loader, load_data
from optim.models import LoadedModel
from optim.snapshots import Code, ModelInfo, Outcome, ParamsFile, Replayed, RunInfo, checkout, read_info, read_inputs, save
from optim.storage import Root

log = logging.getLogger(__name__)
NO_LABELS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class Run:
    run_id: str
    outcomes: list[Outcome]

    @property
    def ok(self) -> bool:
        """Every portfolio was accepted: the run may trade."""
        return all(outcome.accepted for outcome in self.outcomes)


def read_params(model: LoadedModel, path: Path) -> tuple[Params, ParamsFile]:
    """Validate a params file against the model's `Params`, before anything loads, and record where it came from."""
    raw = path.read_bytes()
    try:
        params = model.params.model_validate_json(raw)
    except ValueError as error:
        error.add_note(f"while reading {path} as {model.name}'s params")
        raise
    head, _ = checkout(path.resolve().parent)
    return params, ParamsFile(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), git_commit=head)


def run(
    loader: Loader,
    snapshot_root: Root,
    model: LoadedModel,
    params_file: Path,
    *,
    as_of_date: date,
    portfolio_ids: Sequence[str] | None = None,
    data_dir: Path | None = None,
    workers: int = 1,
    labels: Mapping[str, str] = NO_LABELS,
    clock: Callable[[], datetime] = logs.utc_now,
) -> Run:
    """Load the model's data for `as_of_date` and the portfolios, decide each one, and save the snapshot. `data_dir` reads
    each dataset that has a file there, `<name>.parquet` or `<name>.csv`, instead of its source; without
    `portfolio_ids`, the run decides every portfolio in the holdings file there."""
    params, params_file_info = read_params(model, params_file)
    run_id = str(uuid.uuid7())
    logs.bind(run_id=run_id, model=model.name)
    log.info("starting run", extra={"optim_version": optim.__version__, "model_module": model.module, "params": str(params_file), "as_of_date": as_of_date})
    started_at = clock()
    datasets = declared(model.data)
    if data_dir is not None:
        datasets = {attribute: _from_dir(dataset, data_dir) for attribute, dataset in datasets.items()}
    if portfolio_ids is None:
        portfolio_ids = _portfolios_in(datasets["holdings"])
    portfolio_ids = list(dict.fromkeys(portfolio_ids))
    frames, reads = load_data(loader, datasets, as_of_date, portfolio_ids)
    loaded_at = clock()
    outcomes = decide_all(model, params, model.data(frames, as_of_date=as_of_date), portfolio_ids, workers=workers)
    info = RunInfo(
        run_id=run_id,
        optim_version=optim.__version__,
        model=ModelInfo(name=model.name, module=model.module),
        as_of_date=as_of_date,
        portfolios=portfolio_ids,
        params=params.model_dump(mode="json"),
        params_file=params_file_info,
        labels=dict(labels),
        code=Code.of(model.directory),
        workers=workers,
        started_at=started_at,
        loaded_at=loaded_at,
        finished_at=clock(),
        datasets=reads,
        replay=None,
    )
    save(snapshot_root, info, frames, outcomes)
    return Run(run_id, outcomes)


def replay(
    snapshot: Root,
    snapshot_root: Root,
    *,
    params_file: Path | None = None,
    portfolio_ids: Sequence[str] | None = None,
    workers: int = 1,
    labels: Mapping[str, str] = NO_LABELS,
    clock: Callable[[], datetime] = logs.utc_now,
) -> Run:
    """Decide a saved run again on the datasets its snapshot holds, with the code you have now and, if given, other
    params, and save it as a new run under `snapshot_root`. A replay of a subset of portfolios replays that subset."""
    started_at = clock()
    saved = read_info(snapshot)
    model = models.load(saved.model.name if saved.model.name in models.installed() else saved.model.module)
    run_id = str(uuid.uuid7())
    logs.bind(run_id=run_id, model=model.name)
    log.info("starting replay", extra={"optim_version": optim.__version__, "of": saved.run_id, "params": str(params_file) if params_file else "as saved"})
    params, params_file_info = read_params(model, params_file) if params_file else (model.params.model_validate_json(json.dumps(saved.params)), saved.params_file)
    frames = read_inputs(snapshot, declared(model.data))
    only = list(portfolio_ids) if portfolio_ids else saved.replay.portfolios if saved.replay else None
    if missing := sorted(set(only or ()) - set(saved.portfolios)):
        msg = f"{len(missing)} portfolios are not in {snapshot.location}, e.g. {missing[:5]}"
        raise LookupError(msg)
    decided = [portfolio_id for portfolio_id in saved.portfolios if only is None or portfolio_id in only]
    loaded_at = clock()
    outcomes = decide_all(model, params, model.data(frames, as_of_date=saved.as_of_date), decided, workers=workers)
    info = RunInfo(
        run_id=run_id,
        optim_version=optim.__version__,
        model=ModelInfo(name=model.name, module=model.module),
        as_of_date=saved.as_of_date,
        portfolios=decided,
        params=params.model_dump(mode="json"),
        params_file=params_file_info,
        labels=dict(labels),
        code=Code.of(model.directory),
        workers=workers,
        started_at=started_at,
        loaded_at=loaded_at,
        finished_at=clock(),
        datasets=None,
        replay=Replayed(of=saved.run_id, portfolios=only),
    )
    save(snapshot_root, info, frames, outcomes)
    return Run(run_id, outcomes)


def decide_all(model: LoadedModel, params: Params, data: Datasets, portfolio_ids: Sequence[str], *, workers: int) -> list[Outcome]:
    """Run `prepare` once here, then prepare, decide and check each portfolio in up to `workers` processes."""
    logs.include(model.module.partition(".")[0])
    with logs.timed(log, "prepared"):
        shared = model.prepare(data, params) if model.prepare else data
    state = _State(model.module, params, data, shared)
    with logs.timed(log, "decided portfolios", portfolios=len(portfolio_ids), workers=workers) as fields:
        outcomes = engine.each_portfolio(_decide, state, portfolio_ids, workers=workers, failed=_failed)
        rejected = [outcome.portfolio_id for outcome in outcomes if not outcome.accepted]
        fields.update(rejected=len(rejected), rejected_examples=rejected[:20])
    return outcomes


@dataclass
class _State:
    """What every portfolio's decision shares, shipped once to each worker process."""

    module: str
    params: Params
    data: Datasets
    shared: object

    @cached_property
    def _rows(self) -> dict[str, Mapping[Hashable, ArrayLike]]:
        """Each per-portfolio dataset's row positions, by portfolio id: found once per process, not once per portfolio."""
        per_portfolio = [dataset.name for dataset in declared(type(self.data)).values() if dataset.per_portfolio]
        return {name: self.data.frames[name].groupby("portfolio_id").indices for name in per_portfolio}

    def portfolio(self, portfolio_id: str) -> Datasets:
        """The portfolio's slice of the data: its rows of each per-portfolio dataset, and every other dataset whole. Each
        frame is a shallow copy, so a model that adds a column to one changes no other portfolio's."""
        frames = {name: frame.copy(deep=False) for name, frame in self.data.frames.items()}
        for name, rows in self._rows.items():
            frames[name] = frames[name].take(np.asarray(rows.get(portfolio_id, ()), dtype=np.intp)).reset_index(drop=True)
        return type(self.data)(frames, as_of_date=self.data.as_of_date, portfolio_id=portfolio_id)


def _decide(state: _State, portfolio_id: str) -> Outcome:
    """One portfolio, logged in whichever process decides it. A failure is logged here, where its traceback is whole,
    and rejects this portfolio alone."""
    start = time.perf_counter()
    model = models.load(state.module)
    with logs.fields(portfolio_id=portfolio_id):
        try:
            portfolio = state.portfolio(portfolio_id)
            prepared = model.prepare_portfolio(portfolio, state.shared, state.params) if model.prepare_portfolio else portfolio
            decision = _decision(model, model.decide(prepared, state.shared, state.params))
            reasons = _review(model, prepared, decision)
        except Exception as error:
            log.exception("could not decide %s", portfolio_id)
            return Outcome(portfolio_id=portfolio_id, decision=None, reasons=[f"error: {error!r}"], seconds=time.perf_counter() - start)
        outcome = Outcome(portfolio_id=portfolio_id, decision=decision, reasons=reasons, seconds=time.perf_counter() - start)
        log.info(
            "decided %s: %s",
            portfolio_id,
            "accepted" if outcome.accepted else "rejected",
            extra={"reasons": reasons[:20], "buys": len(decision.buy), "sells": len(decision.sell), "solved": decision.solved, "solver": decision.solver, "seconds": outcome.seconds},
        )
        return outcome


def _decision(model: LoadedModel, decided: object) -> Decision:
    if not isinstance(decided, Decision):
        msg = f"{model.module}.decide returned {type(decided).__name__}, not an optim.Decision"
        raise TypeError(msg)
    return decided


def _review(model: LoadedModel, prepared: object, decision: Decision) -> list[str]:
    """Why the decision may not trade: none when it may. A solver's status alone never makes it acceptable."""
    reasons = [] if decision.solved else [f"not solved: {decision.solver.stop if decision.solver else 'the model says so'}"]
    if model.check is not None:
        reasons += list(model.check(prepared, decision))
    return reasons


def _failed(portfolio_id: str, why: str) -> Outcome:
    return Outcome(portfolio_id=portfolio_id, decision=None, reasons=[why], seconds=0.0)


def _from_dir(dataset: Dataset, directory: Path) -> Dataset:
    for suffix in (".parquet", ".csv"):
        if (file := directory / f"{dataset.name}{suffix}").exists():
            return dataset.file(file)
    return dataset


def _portfolios_in(holdings: Dataset) -> list[str]:
    if holdings.source != "file":
        msg = "give the portfolios to decide: only a run that reads its holdings from a file decides every portfolio in it"
        raise ValueError(msg)
    frame = pd.read_csv(holdings.query, dtype=str) if str(holdings.query).endswith(".csv") else pd.read_parquet(holdings.query, columns=["portfolio_id"])
    return list(dict.fromkeys(frame["portfolio_id"]))
