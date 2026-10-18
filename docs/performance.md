# Performance

Measured run time, CPU and memory of `optim optimize`, run as the CLI against Oracle, SQL Server and Redis on
2026-10-17. Use them to size a Job's CPU and memory requests and to know how long a run should take. They hold for
this host and data shape; measure again when either changes.

## Test setup

| Item | Value |
|---|---|
| Host | AMD Ryzen 7 8745HS: 8 cores, 16 hardware threads; 27 GiB RAM, shared with other services |
| Software | Python 3.14.7, cvxpy 1.9.3, highspy 1.15.1, pandas 3.0.6, numpy 2.5.4, python-oracledb 26.0.1 in thin mode, mssql-python 1.16.0, redis-py 8.1.0 |
| Sources | Oracle Database Free 23ai, SQL Server 2022 and Redis 7.4 in local containers; a local HTTP server stands in for the signals API |
| Market | 100,000 bonds, 30% of them offered; signals for 35,538; benchmarks of 5,000, 2,000 and 300 names |
| Portfolios | 50,000 portfolios with 2.07 million tax lots: 25 to 60 positions each, a fifth of them in two lots, $1mm to $4mm per portfolio. 132,166 lot sales over 60 days, half at a loss |
| Strategy | `strategies/core.json`, with `"overrides": {"US TREASURY": 1.0}` on its issuer limit: an integer solve, 30 s time limit, `mip_rel_gap` 0.001. A solve has about 2,000 bonds, 4,200 variables and 10,000 rows |
| Run | `OPTIM_WORKERS=8`, `OPTIM_LOG_LEVEL=INFO`. The cache was filled by `optim warm` and a first run, except where a row says cold |

The seeding and measuring scripts are not in this repository.

## Batch size

The stage times come from the run's own records: `loaded market`, `loaded portfolios`, `solved portfolios` and
`saved snapshot`. Startup is the rest of the wall time: the interpreter, imports, settings and exit.

| Portfolios | Wall | Startup | Market | Portfolios and lots | Solve | Snapshot | Portfolios a minute |
|---|---|---|---|---|---|---|---|
| 1, cold cache | 2.3 s | 1.0 s | 0.61 s | 0.02 s | 0.63 s | 0.05 s | |
| 1 | 1.9 s | 1.0 s | 0.14 s | 0.02 s | 0.63 s | 0.05 s | |
| 10 | 9.0 s | 1.2 s | 0.14 s | 0.03 s | 7.5 s | 0.06 s | 80 |
| 100 | 55 s | 1.2 s | 0.15 s | 0.04 s | 53 s | 0.16 s | 113 |
| 1,000 | 7 min 38 s | 1.2 s | 0.14 s | 0.12 s | 7 min 35 s | 1.3 s | 132 |

| Portfolios | Peak memory | Parent process | Per solve: median, p95, max | Snapshot | Logs |
|---|---|---|---|---|---|
| 1 | 0.48 GB | 0.48 GB | 0.6 s | 4.2 MB | 3 KB |
| 10 | 1.8 GB | 0.43 GB | 4.1 s, 5.9 s, 6.5 s | 4.3 MB | 14 KB |
| 100 | 2.4 GB | 0.43 GB | 2.2 s, 12.2 s, 26.3 s | 4.7 MB | 124 KB |
| 1,000 | 2.8 GB | 0.50 GB | 2.1 s, 12.7 s, 30.1 s | 8.5 MB | 1.2 MB |

Peak memory is the proportional set size (PSS) of the CLI and its workers together, so pages they share count once.
Parent process is the CLI's resident set.

- From 100 portfolios up, solving is 97% to 99% of the wall time: about 0.45 s per portfolio at 8 workers. The last
  minutes of a batch wait on its slowest solves, up to the time limit.
- One portfolio solves in the CLI's own process. Two or more start a pool of up to `OPTIM_WORKERS` processes.
- The parent stays near 0.5 GB whatever the batch size: a result is a few kilobytes (see
  [What a worker returns](#what-a-worker-returns)). Most of the snapshot is the market, 4.1 MB.
- Of the 1,000 portfolios, 976 solved and 24 were infeasible, each already breaking a limit before any trade. Six
  solves reached the time limit and kept solutions within 1.03% of the best possible.

## Workers

The same 96 portfolios, with `OPTIM_WORKERS` from 2 to 16, OpenBLAS at one thread per worker and `"threads": 1` in the
strategy's `solve.options`:

| Workers | Portfolios a minute | CPU seconds per solve, mean | Peak memory |
|---|---|---|---|
| 2 | 50 | 2.38 | 1.05 GB |
| 4 | 88 | 2.51 | 1.33 GB |
| 6 | 111 | 2.63 | 1.74 GB |
| 8 | 126 | 2.81 | 2.06 GB |
| 12 | 130 | 3.66 | 2.72 GB |
| 16 | 132 | 4.46 | 3.36 GB |

Throughput stops growing at the 8 physical cores. Above 8, solves share cores through hyperthreading, so each takes
longer while the total barely grows. Each worker adds about 0.2 GB for `core`. `track-benchmark` solves are ten times
larger, about 19,000 bonds each: 2 workers replaying them peaked at 1.76 GB.

At 8 workers with OpenBLAS's default of one thread per hardware thread in every worker, the batch solved 111 a minute
at 3.35 CPU seconds per solve. With one OpenBLAS thread per worker, which `engine.py` sets for every pool, it solved
123. One HiGHS thread per worker on top of that gave 126, within the variation between repeated runs.

## What a worker returns

Each solve returns its result to the parent, which keeps it until the snapshot is written.

| Part | Rows per portfolio | Memory per portfolio | Read by |
|---|---|---|---|
| `trades` | 11 | 1 KB | the `solved` log record, the snapshot |
| `positions` | 38 | 1 KB | the snapshot |
| `summary`, `violations` | | under 1 KB | the log, the snapshot's summary, the exit code |

Pickling a result in the worker and unpickling it in the parent takes 0.2 ms. What a solve saw after the rules is not
returned: replay rebuilds it from the snapshot, 0.6 s to load a 1,000-portfolio snapshot, then 31 ms per portfolio.

## Concurrent Jobs

Separate single-portfolio runs started at the same moment on a cold cache, each reading every dataset live:

| Jobs | Wall: min, median, max | Market load | Failures |
|---|---|---|---|
| 8 | 3.7 s, 4.2 s, 5.1 s | 1.2 s to 1.4 s | none |
| 16 | 6.6 s, 8.3 s, 21.3 s | 2.2 s to 3.6 s | none |

## Time to fail

How long a run takes to fail, or how much it slows, when a dependency fails:

| Failure | Effect on the run |
|---|---|
| Redis stopped, or refusing the password | No delay. One warning; every dataset is read live |
| Redis hung | 5 s slower: one socket timeout, then every dataset is read live |
| Corrupt entry in Redis | No delay. That dataset is read live, and the entry replaced |
| Oracle stopped, with the market cached | None: the run never connects to Oracle |
| Oracle stopped, cold cache | Fails in 1 s: `DPY-6005: cannot connect to database` |
| Oracle host unreachable | Fails in 20 s: python-oracledb's `tcp_connect_timeout`, measured with the driver alone |
| Oracle accepts connections and never answers | Fails in 30 s: `CONNECT_TIMEOUT_S` in `sources/oracle.py` |
| SQL Server stopped or hung | Fails in 30 s: `LOGIN_TIMEOUT_S` in `sources/mssql.py`; the driver retries until it |
| SQL Server query blocked by another session's lock | Fails in 240 s: `TIMEOUT_S`, 120 s, applies to the execute and to the fetch |
| Signals API refusing connections | Fails in about 1.5 s, after the client's three connection retries |
| Signals API slow | Waits up to `TIMEOUT_S`, 120 s, per call |
| A solver worker killed | Its unfinished portfolios run again in a new pool; if that breaks too, they stay unsolved |

A blocked Oracle query was not measured. python-oracledb's `call_timeout` applies to each round trip, and a large
query takes several.
