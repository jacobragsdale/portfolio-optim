"""JSON APIs over HTTP: a client factory for every API we call, and `RestSource`, a source for APIs that answer a GET with a
list of records."""

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import HttpUrl, TypeAdapter

from optim.logs import ms_since
from optim.schema import Model
from optim.sources import TIMEOUT_S, Columns, Param, columns_from_records

log = logging.getLogger(__name__)


class ApiSettings(Model):
    url: HttpUrl


def client(settings: ApiSettings) -> httpx.Client:
    """A client with a deadline on every call, which logs each call and raises on any 4xx or 5xx reply, so callers need
    not check. Its retries cover only failed connections, so they are safe for POSTs."""
    transport = httpx.HTTPTransport(retries=3)
    return httpx.Client(base_url=str(settings.url), timeout=TIMEOUT_S, transport=transport, event_hooks={"request": [_start], "response": [_check]})


def _start(request: httpx.Request) -> None:
    request.extensions["optim_start"] = time.perf_counter()


def _check(response: httpx.Response) -> None:
    response.read()  # so `ms` runs to the last byte; the caller gets the same body
    request = response.request
    log.debug("http", extra={"method": request.method, "url": str(request.url), "status": response.status_code, "ms": ms_since(request.extensions["optim_start"])})
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        error.add_note(f"the server replied: {response.text[:2000]}")  # its reply usually says what was wrong
        raise


_RECORDS = TypeAdapter(list[dict[str, object]])


@dataclass(frozen=True)
class RestSource:
    client: httpx.Client

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        response = self.client.get(query, params={k: _query_value(v) for k, v in params.items()})
        return columns_from_records(_RECORDS.validate_json(response.content), source=query)


def _query_value(value: Param) -> str | int | float | list[str]:
    match value:
        case date():
            return value.isoformat()
        case tuple():
            return list(value)
        case _:
            return value
