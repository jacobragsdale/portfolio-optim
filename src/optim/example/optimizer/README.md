# Optimizer

The cvxpy problem a portfolio's decision solves.

| File | Holds | Change it to |
|---|---|---|
| `solve.py` | `solve(portfolio, params)`: build the problem, add the model's own constraints, solve it | change how a portfolio is solved, or what it reports |
| `problem.py` | `build(universe, constraints, objective, value=...)`: the constraints and the objective, compiled from their dicts | add a kind of constraint or objective term to the stand-in |
| `constraints.py` | The constraints written in cvxpy here: trade sizes and cash | add a constraint the definitions cannot express |

`problem.py` stands in for the firm's cvxpy wrapper, which compiles the same kind of dicts in a firm model; its
docstring lists the kinds it knows. A constraint usually needs no code: add it to `constraints` in the params, or have a
rule add it for one portfolio. To move to the wrapper, delete `problem.py` and build with the wrapper in `solve.py`;
`constraints.py` works on the wrapper's `buy` and `sell` as it does on these.

`uv run optim docs how-to/add-a-constraint` and `uv run optim docs how-to/change-the-objective` say more.
