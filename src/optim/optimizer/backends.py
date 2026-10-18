"""The solvers a strategy can name, through cvxpy: what each can solve, how it takes the strategy's solve settings, and
how it reports why it stopped.

cvxpy maps every solver's status onto its own few, and loses what acceptance needs: COPT stopped by its time limit reads
as `optimal_inaccurate` with a solution and `infeasible_inaccurate` without one. So each backend reads its solver's own
status into a `Termination`, and the rest of the engine reads only that. A strategy that a solver cannot solve, such as
a quadratic objective with integer trades on HiGHS, is refused when the strategy is read: the engine never swaps a
solver, nor a quadratic term for a linear one.

To add a solver cvxpy supports: a class here with its options and its termination, an entry in `SOLVERS`, and its
package as an optional dependency."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

import cvxpy as cp
import highspy

type SolverName = Literal["HIGHS", "COPT"]
type Stop = Literal["optimal", "limit", "imprecise", "infeasible", "unbounded", "failed"]


@dataclass(frozen=True)
class Termination:
    """Why a solve stopped, in the engine's words."""

    stop: Stop
    """`optimal`: proved optimal, within the solver's tolerances. `limit`: stopped by a limit, the time limit unless
    options set another, before proving it. `imprecise`: the solver doubts its own answer. The rest found nothing."""
    solver_status: str  # the solver's own status, for diagnosis
    has_solution: bool  # a feasible solution to read
    gap: float | None  # integer solves with a solution: how far it may be from the best possible, relative, as the solver reports it
    nodes: int | None  # integer solves: branch-and-bound nodes explored


class Backend(Protocol):
    mixed_integer_quadratic: bool  # solves a quadratic objective with integer variables

    def options(self, time_limit_s: float, options: Mapping[str, object]) -> dict[str, object]:
        """Keyword arguments for `cp.Problem.solve`: the time limit and its defaults under the solver's own names, then
        the strategy's `options`, which override them."""
        ...

    def termination(self, problem: cp.Problem, *, integer: bool) -> Termination: ...


class Highs:
    """HiGHS, free: LP, MILP, and QP without integers."""

    mixed_integer_quadratic = False
    _STOPS: Mapping[str, Stop] = {cp.OPTIMAL: "optimal", cp.USER_LIMIT: "limit", cp.OPTIMAL_INACCURATE: "imprecise", cp.INFEASIBLE: "infeasible", cp.UNBOUNDED: "unbounded"}

    def options(self, time_limit_s: float, options: Mapping[str, object]) -> dict[str, object]:
        return {"time_limit": time_limit_s, **options}

    def termination(self, problem: cp.Problem, *, integer: bool) -> Termination:
        info = problem.solver_stats.extra_stats
        if not isinstance(info, highspy.HighsInfo):
            msg = f"expected HiGHS's solver stats, got {type(info).__name__}"
            raise TypeError(msg)
        feasible = info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible.value
        stop = self._STOPS.get(problem.status, "failed")
        has_solution = stop in ("optimal", "imprecise") or (stop == "limit" and feasible)
        gap = info.mip_gap if integer and has_solution and math.isfinite(info.mip_gap) else None
        return Termination(stop, f"cvxpy {problem.status}, primal solution status {info.primal_solution_status}", has_solution, gap, info.mip_node_count if integer else None)


class Copt:
    """COPT, licensed: LP, MILP, and convex QP and MIQP. Install it with `uv sync --extra copt`, and put its license file
    where COPT looks, such as `COPT_LICENSE_DIR`. Without a license it runs size-limited, enough for tests."""

    mixed_integer_quadratic = True
    # COPT's own status codes, from its reference manual
    _STOPS: Mapping[int, Stop] = {1: "optimal", 2: "infeasible", 3: "unbounded", 4: "infeasible", 6: "limit", 7: "imprecise", 8: "limit", 10: "limit", 11: "limit"}

    def options(self, time_limit_s: float, options: Mapping[str, object]) -> dict[str, object]:
        # one thread: the engine runs one solve per worker process, a worker per core
        return {"TimeLimit": time_limit_s, "Threads": 1, **options}

    def termination(self, problem: cp.Problem, *, integer: bool) -> Termination:
        import coptpy  # noqa: PLC0415 -- an optional dependency: only a COPT solve needs it, and cvxpy has imported it by now

        if not isinstance(model := problem.solver_stats.extra_stats, coptpy.Model):
            msg = f"expected COPT's model as its solver stats, got {type(model).__name__}"
            raise TypeError(msg)
        code = int(model.status)
        stop = self._STOPS.get(code, "failed")
        has_solution = bool(model.hasmipsol) if integer else stop in ("optimal", "imprecise") and bool(model.haslpsol)
        gap = float(model.getAttr("BestGap")) if integer and has_solution else None
        return Termination(stop, f"COPT status {code}", has_solution, gap, int(model.getAttr("NodeCnt")) if integer else None)


SOLVERS: Mapping[SolverName, Backend] = {"HIGHS": Highs(), "COPT": Copt()}
