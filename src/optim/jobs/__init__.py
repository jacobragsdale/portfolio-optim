"""Entry points, one module each. A job takes the process `Context` and a validated request, the same model whether the
CLI or the API built it, and runs load -> solve -> snapshot -> act from shared parts. The request is saved in the
snapshot, so every run records exactly what was asked."""

from dataclasses import dataclass

from optim.optimizer.results import Result, Solved


@dataclass(frozen=True)
class Run:
    run_id: str  # names the run's snapshot directory
    results: list[Result]

    @property
    def ok(self) -> bool:
        """Every portfolio solved, and rounding broke nothing."""
        return all(isinstance(r, Solved) and r.ok for r in self.results)
