"""Entry points. A job is its request, one module each: a pydantic model of what was asked, the same whether the CLI or
an API built it, with `portfolio_ids`, the portfolios it loads, `holdings`, which holdings they start from, and `tasks`,
the solves it makes from the loaded datasets. `run.run` loads, prepares, solves and saves any job, and `run.replay` runs
it again from its snapshot. A new job is a request class added to `Job`, and a CLI subcommand."""

from pydantic import TypeAdapter

from optim.jobs.optimize import OptimizeRequest

# TODO(port): the old system's other entry points, such as model portfolios or transition analysis, are request classes
# beside this one, joined here in a union discriminated on `job`. See docs/extending.md.
Job = OptimizeRequest
JOB: TypeAdapter[Job] = TypeAdapter(Job)
