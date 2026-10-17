"""Logging. Each record is one JSON object per line on stderr, for agents and Azure Monitor; `OPTIM_LOG_FORMAT=text`
prints lines for a person instead. INFO tells a run's story: what loaded, each portfolio's result and trades, what was
saved and sent. `OPTIM_LOG_LEVEL=DEBUG` adds a record for every load (with its cache outcome), query, HTTP call, rule
and LP solve, with timings in `*ms` fields.

To log, use stdlib logging and put data in `extra`. Each key becomes a JSON field, and frames, series, numpy values,
dates and pydantic models serialize as they are:

    log = logging.getLogger(__name__)
    log.debug("rule applied", extra={"rule": rule, "dropped": 12})

Keys that clash with `LogRecord` attributes (`name`, `module`, `filename`, ...) raise. Keep records small: AKS splits
lines over 16KB, so bulk data belongs in the run's snapshot, which the logs point to. To time a block, use `timed`.

Exceptions need nothing: the CLI and the engine log them once, with the notes added on the way up, the root cause, and
the line in this package where it surfaced. Where a frame knows what the message does not, such as which dataset was
loading, it adds that with `error.add_note(...)`.
"""

import logging
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, override

import numpy as np
import pandas as pd
import pydantic_core
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE = Path(__file__).parent
_STANDARD = {*vars(logging.makeLogRecord({})), "message", "asctime"}  # LogRecord's own attributes, not `extra` fields


class LogSettings(BaseSettings):
    """Read on its own, before `Settings`, so that a bad environment is logged like any other error."""

    model_config = SettingsConfigDict(env_prefix="OPTIM_LOG_", strict=True, extra="forbid", frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"


def setup() -> None:
    """Configure this process: the CLI calls it first, and each solver worker as it starts. `optim` loggers log at the
    configured level; libraries log only warnings and up."""
    settings = LogSettings()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if settings.format == "json" else TextFormatter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    logging.getLogger("optim").setLevel(settings.level)


@contextmanager
def timed(logger: logging.Logger, event: str, *, level: int = logging.DEBUG, **fields: object) -> Generator[dict[str, object]]:
    """Log `event` when the block ends, with `fields`, any the block adds to the dict it gets, and `ms`. A block that
    raises logs nothing here: its exception tells that story."""
    start = time.perf_counter()
    yield fields
    logger.log(level, event, extra=fields | {"ms": ms_since(start)})


def ms_since(start: float) -> float:
    """Milliseconds since a `time.perf_counter()` reading."""
    return round((time.perf_counter() - start) * 1000, 1)


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info and record.exc_info[1]:
            entry |= describe(record.exc_info[1])
        return to_json(entry)


class TextFormatter(logging.Formatter):
    """For a person at a terminal: the message, its fields as JSON, then the traceback as Python prints it."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    @override
    def formatMessage(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        return super().formatMessage(record) + (f" {to_json(extras)}" if extras else "")


def describe(error: BaseException) -> dict[str, object]:
    """An exception as an agent needs it: what went wrong, the notes added on the way up, the exception at the bottom of
    the chain, the line in this package where it surfaced (even when a library raised it), and the full traceback."""
    chain = [error]
    while (cause := _cause(chain[-1])) is not None and cause not in chain:
        chain.append(cause)
    return {
        "error": _headline(error),
        "notes": [note for e in chain for note in getattr(e, "__notes__", [])],
        "root_cause": _headline(chain[-1]),
        "where": next((where for e in reversed(chain) if (where := _where(e))), None),
        "traceback": "".join(traceback.format_exception(error)),
    }


def to_json(value: object) -> str:
    return pydantic_core.to_json(value, fallback=_fallback, inf_nan_mode="null").decode()


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {key: value for key, value in vars(record).items() if key not in _STANDARD}


def _fallback(value: object) -> object:
    match value:
        case pd.DataFrame():
            return value.to_dict("records")
        case pd.Series():
            return value.to_dict()
        case pd.Index() | np.ndarray() | np.generic():
            return value.tolist()
        case _:
            return str(value)


def _cause(error: BaseException) -> BaseException | None:
    return error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def _headline(error: BaseException) -> str:
    kind = type(error)
    name = kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
    return f"{name}: {error}"


def _where(error: BaseException) -> str | None:
    """The deepest frame of `error` in this package, as `optim/module.py:line in function`."""
    ours = [frame for frame in traceback.extract_tb(error.__traceback__) if Path(frame.filename).is_relative_to(_PACKAGE)]
    return f"{Path(ours[-1].filename).relative_to(_PACKAGE.parent)}:{ours[-1].lineno} in {ours[-1].name}" if ours else None
