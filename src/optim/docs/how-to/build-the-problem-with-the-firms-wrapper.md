# How to build the problem with the firm's wrapper

Build a model's cvxpy problem with the firm's cvxpy wrapper instead of the example's stand-in, so its constraints and
objective are compiled from the wrapper's own vocabulary. The model's folder stays as `optim new` made it but for one
file: `optimizer/problem.py`, the stand-in, goes, and `optimizer/solve.py` calls the wrapper instead.

This guide is a placeholder until the wrapper's owners complete it. The wrapper is internal, so the steps marked "for
the wrapper's owners" say what the model needs from its API, not how to call it.

Prerequisites:

- A model made by `optim new`, with its tests passing.
- The wrapper installed in the repo: `uv add WRAPPER`, with the firm's index configured as for `optim`.

To build the problem with the wrapper:

1. Write the model's constraints and objective in the wrapper's vocabulary: `constraints` and `objective` in the
   params, and the dicts the rules add, such as `rules/mandates.py`'s, keep their place but change their keys. Neither
   `optim` nor the model defines the kinds; the wrapper does.
2. Delete `optimizer/problem.py`, and in `optimizer/solve.py` build with the wrapper. What it needs from the wrapper: an
   unsolved `cp.Problem`, built from the portfolio's universe, its constraint dicts and its objective dicts, and the
   cvxpy expressions of the quantity bought and sold, one entry per row of the universe. For the wrapper's owners: the
   call that does this, and the arguments it takes.
3. Keep `optimizer/constraints.py`. Its constraints work on the wrapper's `buy` and `sell` as they do on the
   stand-in's, and `solve.py` adds them to the wrapper's problem as before:

   ```python
   own = [*constraints.sizes(universe, buy, sell, value=value), *constraints.cash(portfolio, buy, sell)]
   full = cp.Problem(problem.objective, [*problem.constraints, *own])
   return optim.cvxpy.solve(full, params.solve, securities=universe["security_id"].tolist(), buy=buy, sell=sell, metrics=metrics)
   ```

   Drop the sizes, minimum pieces and whole-lot sales from `constraints.py` where the wrapper already makes them. For
   the wrapper's owners: whether it does.
4. Report the metrics `Metrics` declares, as a dict of cvxpy expressions or numbers by name: the stand-in hands back
   each objective term's expression as `built.terms`. For the wrapper's owners: whether it hands back its terms.
5. If the wrapper solves the problem itself, read its answer with `optim.cvxpy.read` instead of solving it with
   `optim.cvxpy.solve`. `read` turns a solved problem into the `Decision`, with the solver's own status, so a solve
   that stopped at its time limit is still rejected:

   ```python
   return optim.cvxpy.read(problem, securities=universe["security_id"].tolist(), buy=buy, sell=sell, metrics=metrics)
   ```

   The model's own constraints must then reach the wrapper before it solves. For the wrapper's owners: how it takes
   extra cvxpy constraints.
6. Change the tests that name the stand-in's kinds, in `tests/test_optimizer.py`, to the wrapper's, and the kinds listed
   in the model's `README.md`.

To confirm it worked, run `uv run pytest`: the contract test decides every portfolio of the fixture through the wrapper,
and its import check fails if the wrapper reaches a database or an API, which model code must not. Then run the model
on a saved run with `replay` and compare its trades with the stand-in's.

For what the stand-in compiles, see [how to add a constraint](add-a-constraint.md) and
[how to change the objective](change-the-objective.md).
