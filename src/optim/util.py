"""Small helpers shared across the package."""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager

log = logging.getLogger(__name__)


@contextmanager
def timed(event: str, **context: object) -> Generator[None]:
    """Log how long the block took; `context` goes in the message and as structured `extra` fields."""
    start = time.perf_counter()
    yield
    log.info("%s took %.0fms %s", event, (time.perf_counter() - start) * 1000, context, extra=context)
