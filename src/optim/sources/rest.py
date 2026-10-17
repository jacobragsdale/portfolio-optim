"""JSON APIs over HTTP: a client factory for every API we call, and `Rest`, a source for APIs that answer a GET with a
list of records."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import HttpUrl, TypeAdapter

from optim.schema import Strict
from optim.sources import TIMEOUT_S, Columns, Param, columns_from_records


class ApiSettings(Strict):
    url: HttpUrl


def client(settings: ApiSettings) -> httpx.Client:
    """A client with a deadline on every call. Its retries cover only failed connections, so they are safe for POSTs."""
    return httpx.Client(base_url=str(settings.url), timeout=TIMEOUT_S, transport=httpx.HTTPTransport(retries=3))


_RECORDS = TypeAdapter(list[dict[str, object]])


@dataclass(frozen=True)
class Rest:
    client: httpx.Client

    def fetch(self, query: str, params: Mapping[str, Param]) -> Columns:
        response = self.client.get(query, params={k: _query_value(v) for k, v in params.items()})
        response.raise_for_status()
        return columns_from_records(_RECORDS.validate_json(response.content), source=query)


def _query_value(value: Param) -> str | int | float | list[str]:
    match value:
        case date():
            return value.isoformat()
        case tuple():
            return list(value)
        case _:
            return value
