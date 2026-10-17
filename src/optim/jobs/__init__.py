"""Entry points, one module each. A job takes the process `Context` and a validated request, which the CLI builds today
and an API or agent can build tomorrow, and runs load -> solve -> snapshot -> act from shared parts. The request is
saved in the snapshot, so every run records exactly what was asked."""
