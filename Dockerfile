# syntax=docker/dockerfile:1
# The `optim` CLI, which Airflow runs with the command's arguments, e.g.
# `optimize --strategy /strategies/core.json --portfolios A1 A2`.

FROM python:3.14.7-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first, so a code change rebuilds only the last layer.
# With COPT, which a strategy can name; it reads its license from COPT_LICENSE_DIR.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --extra copt --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --extra copt --no-editable

FROM python:3.14.7-slim-trixie
# mssql-python's bundled ODBC driver links against these. Oracle's thick mode would also need Oracle Instant Client and
# libaio here, and OPTIM_ORACLE__MODE=thick; thin mode, the default, needs neither.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home optim
COPY --from=build /opt/venv /opt/venv
# Strategy files ship with the code, so the commit below names them too: a Job runs `--strategy /strategies/core.json`.
COPY strategies /strategies
# The commit, which each run's snapshot records. Declared last, so a new commit rebuilds no layer above.
ARG GIT_SHA
RUN test -n "$GIT_SHA" || { echo "build with --build-arg GIT_SHA=\$(git rev-parse HEAD): a run must record its code" >&2; exit 1; }
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 OPTIM_GIT_SHA=$GIT_SHA
USER 10001
ENTRYPOINT ["optim"]
CMD ["--help"]
