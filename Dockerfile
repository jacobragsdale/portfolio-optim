# syntax=docker/dockerfile:1
# The `optim` CLI. Every run is an AKS Job that Airflow starts with the command's arguments, e.g.
# `optimize --strategy /strategies/core.json --accounts A1 A2`. deploy/job.yaml is the Job spec.

FROM python:3.14.7-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first, so a code change rebuilds only the last layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.14.7-slim-trixie
# mssql-python's bundled ODBC driver links against these.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home optim
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
ENTRYPOINT ["optim"]
CMD ["--help"]
