# How to change the objective

Change what a model maximizes, for every run or for one params file: weigh trading costs differently, add a term such
as a penalty above a soft cap, or drop one. The objective is a list of terms, dicts in the vocabulary of the model's
`optimizer/problem.py`, as the firm's cvxpy wrapper's are in a firm model. Its default is in code, and a params file
may replace it.

Prerequisites:

- A model made by `optim new`, with its tests passing.

To change the objective for one params file, such as a variant a sweep compares:

1. Set `objective` in the params file. It replaces the whole default list, so list every term the variant keeps:

   ```json
   "objective": [{"term": "income"}, {"term": "cost", "bps": 15.0}]
   ```

   The example's stand-in knows two terms, which it sums and maximizes as a fraction of the portfolio's value:
   `{"term": "income"}`, expected income over a year after trading, and `{"term": "cost", "bps": 5.0}`, less the
   trading cost, in basis points of the value traded.
2. Decide a saved run with it, beside the default, in one table. In `main.py`:

   ```python
   table = sweep(model, "RUN_ID", params=["params/default.json", "params/costly.json"])
   ```

   Each row is a params file and a portfolio, with its objective, turnover and each term's amount as a metric.

To change the objective for every run, change `OBJECTIVE` in `model.py`, which `Params.objective` defaults to:

```python
OBJECTIVE: list[Definition] = [{"term": "income"}, {"term": "cost", "bps": 5.0}]
"""What the model maximizes, unless the params say otherwise: see `optimizer/problem.py` for the terms."""
```

`default.json` leaves `objective` out, so it takes this default. Changing it changes what every portfolio decides, so
the behaviour tests that solve, in `tests/test_optimizer.py`, may need new expectations; the contract test still decides
every portfolio of the fixture.

To add a term:

1. In `optimizer/problem.py`, write its model beside the others, and add it to `_TERMS`. This one is a soft cap: a
   penalty on each bond's weight above `over`, which the objective trades against income:

   ```python
   class Excess(Model):
       """A penalty on each bond's weight above `over`, of `penalty` times the excess value: a soft cap, which the
       objective trades against income."""

       term: Literal["excess"]
       over: float = Field(ge=0)
       penalty: float = Field(ge=0)


   _TERMS = TypeAdapter(list[Annotated[Income | Cost | Excess, Field(discriminator="term")]])
   ```

2. Compile it in `build`'s loop over the terms. Each term's amount, in the portfolio's currency, goes in `terms`, which
   the decision reports as metrics; what it adds to the objective, with its sign, goes in `signed`:

   ```python
   if isinstance(term, Income):
       terms["income"] = (universe["expected_return"].to_numpy(dtype=float) * unit) @ (held + buy - sell)
       signed.append(terms["income"])
   elif isinstance(term, Cost):
       terms["cost"] = (unit * term.bps / 1e4) @ (buy + sell)
       signed.append(-terms["cost"])
   else:
       terms["excess"] = term.penalty * value * cp.sum(cp.pos(weights - term.over))
       signed.append(-terms["excess"])
   ```

   A term must keep the problem one the solver solves. The example's problem has integer variables, for trade sizes, and
   HiGHS solves linear and piecewise-linear terms, such as `cp.pos` of a linear expression, alongside them, but not
   quadratic ones; COPT solves those too (see the `solve` settings in the [reference](../reference.md)).
3. List it in the module's docstring, as `{"term": "excess", "over": 0.03, "penalty": 0.1}`, and test it in
   `tests/test_optimizer.py`:

   ```python
   def test_an_excess_term_holds_weights_toward_its_soft_cap() -> None:
       found = portfolio({"security_id": "A"}, {"security_id": "B"})
       soft = [*found.objective, {"term": "excess", "over": 0.03, "penalty": 0.1}]

       capped = decide(replace(found, objective=soft), {"kind": "weight", "per": "security_id", "max": 0.1})

       assert capped.buy == {"A": 30_000.0, "B": 30_000.0}
       assert capped.metrics["excess"] == pytest.approx(0.0)
   ```

4. If the term is in `OBJECTIVE`, declare its metric in `Metrics` in `model.py`, with a docstring. The contract test
   requires the fixture's decisions to report exactly the metrics `Metrics` declares, and fails otherwise:

   ```text
   income's decisions report metrics its Metrics does not declare, or declares metrics they do not report: ['excess']
   ```

   A term that only a variant uses is not declared: its metric appears in that variant's rows of a sweep alone.

To confirm it worked, run `uv run pytest`, then sweep a saved run with a params file that uses the term, as in step 2
of the first procedure, and compare its rows with the default's. Describe the objective, in business terms, under
"What it maximizes" in the model's `README.md`.

A rule may change the objective for one portfolio, as it may its constraints, by returning `portfolio.objective`
changed (see [how to write a rule](write-a-rule.md)). For limits rather than preferences, see
[how to add a constraint](add-a-constraint.md).
