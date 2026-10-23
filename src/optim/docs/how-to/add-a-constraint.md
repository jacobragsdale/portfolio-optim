# How to add a constraint

Limit what a portfolio's decision may do, such as a cap on a sector, a client's own turnover limit, or how many bonds
it buys. A constraint is a dict that the model's `optimizer/problem.py` compiles into cvxpy, as the firm's cvxpy
wrapper does in a firm model, or cvxpy the model writes in `optimizer/constraints.py`. Constraints come from the
params, from a dataset, from code, or from a rule, and all of them apply: where two cap the same thing, the tighter
binds.

Prerequisites:

- A model made by `optim new`, with its tests passing.
- For a constraint from a dataset, the dataset (see [how to add a dataset](add-a-dataset.md)).

## Add one for every portfolio, in the params

1. Add it to `constraints` in `params/default.json`, in the vocabulary `optimizer/problem.py`'s docstring lists. The
   example's stand-in knows three kinds:

   ```json
   "constraints": [
     {"kind": "weight", "per": "security_id", "max": 0.05},
     {"kind": "weight", "per": "issuer", "max": 0.1},
     {"kind": "turnover", "max": 0.25},
     {"kind": "weight", "where": {"sector": "Industrials"}, "max": 0.2}
   ]
   ```

   A weight cap with `per` caps each group of the universe's rows, by any column, such as `issuer` or `sector`; one
   with `where` caps the rows it picks, together; and either leaves the portfolio what it may not sell, however much it
   weighs. `turnover` caps the value bought plus sold, as a fraction of the portfolio's value. In a firm model, the
   kinds are the wrapper's.
2. Run `uv run pytest`. A constraint `build` does not know rejects every portfolio of the fixture, so the contract test
   fails, naming it:

   ```text
   P1: ValidationError: 1 validation error for list[tagged-union[Weight,Turnover]] ...
     Input tag 'wieght' found using 'kind' does not match any of the expected tags: 'weight', 'turnover'
   ```

A variant params file with a constraint more or less is how a sweep compares them (see
[how to compare params with a sweep](compare-params-with-a-sweep.md)). A constraint that is not listed is not applied.

## Add one per portfolio, from a dataset

A client's own limits are rows of a dataset, which a rule turns into dicts. The example's `mandates` dataset and
`rules/mandates.py` add a sector cap for each mandate; this adds a client's own turnover limit the same way.

1. Declare the dataset, read per portfolio, with a row per limit (see [how to add a dataset](add-a-dataset.md)), and
   add a CSV of it to the fixture:

   ```python
   class TurnoverLimit(Row):
       portfolio_id: Id
       max_turnover: Weight
       """The most the portfolio may buy plus sell, as a fraction of its value."""


   TURNOVER_LIMITS = Dataset("turnover_limits", TurnoverLimit, source="mssql", query=SQL / "turnover_limits.sql", key=("portfolio_id",))
   ```

2. Write a rule that turns the portfolio's rows into constraints, added to the params' own, in `rules/limits.py`:

   ```python
   """Limits a client set on their own portfolio, as constraints on top of the params' own."""

   from dataclasses import replace

   from models.income import model


   def turnover(portfolio: model.Portfolio, market: model.Market, params: model.Params) -> model.Portfolio:
       """The portfolio's own turnover limit, where it has one, as a turnover constraint: it applies with the params'
       limit, so the tighter binds."""
       limits = [{"kind": "turnover", "max": row.max_turnover} for row in portfolio.data.turnover_limits.itertuples()]
       return replace(portfolio, constraints=[*portfolio.constraints, *limits])
   ```

3. Call it in `prepare_portfolio` in `model.py`, after `mandates.constraints`:
   `portfolio = limits.turnover(portfolio, market, params)`.

How constraints from different places combine is the rules' logic: a rule may also remove or loosen one of the params'
constraints for a portfolio, by returning `portfolio.constraints` changed. See [how to write a rule](write-a-rule.md).

## Write one in cvxpy

A constraint the dicts cannot express is cvxpy in `optimizer/constraints.py`, on the `buy` and `sell` that
`problem.build` makes, one entry per row of the universe. The trade sizes and cash are there already. This one caps how
many bonds a portfolio buys:

1. Write it in `optimizer/constraints.py`:

   ```python
   def most_buys(universe: pd.DataFrame, buy: cp.Variable, *, value: float, count: int) -> list[cp.Constraint]:
       """At most `count` bonds bought: each buy needs its bond chosen, and at most `count` are chosen."""
       chosen = cp.Variable(len(universe), boolean=True)
       return [buy <= cp.multiply(value / universe["unit_value"].to_numpy(dtype=float), chosen), cp.sum(chosen) <= count]
   ```

2. Add it to the model's own constraints in `optimizer/solve.py`:

   ```python
   own = [
       *constraints.sizes(universe, built.buy, built.sell, value=value),
       *constraints.cash(portfolio, built.buy, built.sell),
       *constraints.most_buys(universe, built.buy, value=value, count=params.max_buys),
   ]
   ```

3. Add its param to `Params` in `model.py`, with a docstring and a default, and to `params/default.json`:

   ```python
   max_buys: int = Field(default=10, ge=0)
   """The most bonds a portfolio buys in one rebalance."""
   ```

4. Test it in `tests/test_optimizer.py`, which solves a portfolio of a few bonds:

   ```python
   def test_most_buys_caps_how_many_bonds_are_bought() -> None:
       found = portfolio({"security_id": "A"}, {"security_id": "B"}, {"security_id": "C"})

       decision = solve.solve(replace(found, constraints=[ROOM, {"kind": "weight", "per": "security_id", "max": 0.1}]), params(max_buys=2))

       assert len(decision.buy) == 2
   ```

## Add a kind of constraint to the stand-in

To make a new kind available as a dict, in the params and to rules, teach `optimizer/problem.py` to compile it. In a
firm model, this is the wrapper's to add. This adds a cap on sales alone:

1. Write its model beside the others, and add it to `_CONSTRAINTS`:

   ```python
   class Sales(Model):
       """The value sold at most `max` of the portfolio's value."""

       kind: Literal["sales"]
       max: float = Field(ge=0)


   _CONSTRAINTS = TypeAdapter(list[Annotated[Weight | Turnover | Sales, Field(discriminator="kind")]])
   ```

2. Compile it in `build`'s loop over the constraints:

   ```python
   if isinstance(constraint, Turnover):
       limits.append((unit / value) @ (buy + sell) <= constraint.max)
   elif isinstance(constraint, Sales):
       limits.append((unit / value) @ sell <= constraint.max)
   else:
       ...
   ```

3. List it in the module's docstring, as `{"kind": "sales", "max": 0.1}`, and test it in `tests/test_optimizer.py`:

   ```python
   def test_a_sales_cap_caps_what_is_sold() -> None:
       """A is 20% of P1's 1,250,000, over its cap of 15%: P1 sells at least 62,500 of it, and at most 10% of its value."""
       found = portfolio({"security_id": "A", "held": 250_000.0})

       decision = decide(found, {"kind": "weight", "per": "security_id", "max": 0.15}, {"kind": "sales", "max": 0.1})

       assert decision.solved
       assert 62_500.0 <= decision.sell["A"] <= 125_000.0
   ```

To confirm it worked, run `uv run pytest`, then run the model on its fixture and compare the trades with a run before
the change. Name the constraint, in business terms, under "Constraints" in the model's `README.md`.

A constraint needs no second check after the solve. The solver proved its decision meets every constraint before the
core rounds it, and the core rejects a decision that rounding moves by more than a millionth of the portfolio's value,
so what the solver proved still holds. A portfolio whose constraints cannot all be met is rejected as `not solved:
infeasible`. For the objective, see [how to change the objective](change-the-objective.md).
