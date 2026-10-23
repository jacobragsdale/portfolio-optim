"""The process's connections, opened once per command: the CLI opens one. Every source, the cache and the snapshot root
are wired here and nowhere else. A source the environment does not configure is not opened, and a dataset that reads
from it fails when it loads, naming the settings it needs."""

import threading
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from optim.data.cache import FileCache
from optim.data.loader import Loader
from optim.settings import Settings
from optim.sources import Source, SourceName, mssql, oracle, rest
from optim.sources.file import FileSource
from optim.storage import Root


@dataclass(frozen=True)
class Context:
    loader: Loader
    snapshot_root: Root


@contextmanager
def open_context(settings: Settings, *, cache: bool = True) -> Generator[Context]:
    """Everything closes on exit. `cache=False`, or no cache root, reads every dataset live."""
    snapshot_root = Root.at(settings.snapshot_root)
    snapshot_root.make_dir()  # storage that cannot be written fails the run before it loads, not after it decides
    with ExitStack() as stack:
        sources: dict[SourceName, Source] = {"file": FileSource()}
        limits: dict[SourceName, threading.Semaphore] = {}
        if settings.oracle:
            sources["oracle"], limits["oracle"] = oracle.connect(settings.oracle), threading.Semaphore(settings.oracle.max_concurrent)
        if settings.mssql:
            sources["mssql"], limits["mssql"] = mssql.connect(settings.mssql), threading.Semaphore(settings.mssql.max_concurrent)
        if settings.rest:
            sources["rest"] = rest.RestSource(stack.enter_context(rest.client(settings.rest)), settings.rest.max_rows)
            limits["rest"] = threading.Semaphore(settings.rest.max_concurrent)
        yield Context(Loader(sources, open_cache(settings) if cache else None, limits), snapshot_root)


def open_cache(settings: Settings) -> FileCache | None:
    """The dataset cache, or None without `OPTIM_CACHE_ROOT`."""
    return FileCache(Root.at(settings.cache_root)) if settings.cache_root else None
