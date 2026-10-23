# How to decide portfolios one at a time

Make portfolios that compete for a limited supply, such as a new issue or a scarce bond, decide one at a time, each
seeing what the portfolios before it left. Define `reserve` in the model, and `order` to choose who goes first.

Prerequisites:

- A dataset of what is on offer, such as an `offerings` dataset with a `security_id` and a `quantity` per row (see
  [how to add a dataset](add-a-dataset.md)), in the model's `Data`.
- A model with `prepare`, whose result every portfolio's `decide` receives as `shared`; the cvxpy example's returns a
  `Market`.

To decide portfolios one at a time:

1. Make `prepare` return the supply as part of the shared state, in a frozen dataclass or another value that pickles.
   In the cvxpy example, add a field to `Market`:

   ```python
   @dataclass(frozen=True)
   class Market:
       securities: pd.DataFrame
       left: dict[str, float]
       """Quantity still on offer, by security id."""


   def prepare(data: Data, params: Params) -> Market:
       left = dict(zip(data.offerings["security_id"], data.offerings["quantity"], strict=True))
       return Market(rules.market(data.securities, data.signals), left)
   ```

2. In `decide`, buy no more than `shared` has left of each security. In the cvxpy example, `buy` has one entry per row
   of `lines`, so cap it by security:

   ```python
   left = np.array([shared.left.get(security, np.inf) for security in lines["security_id"]])
   constraints.append(buy <= left)
   ```

3. Define `reserve`, which takes the shared state and an accepted decision, rounded, and returns the state the next
   portfolio decides with. A decision's `buy` maps security ids to quantities. Return a new value rather than changing
   the old one:

   ```python
   def reserve(shared: Market, decision: optim.Decision) -> Market:
       left = {security: quantity - decision.buy.get(security, 0.0) for security, quantity in shared.left.items()}
       return Market(shared.securities, left)
   ```

   Defining `reserve` is what makes the portfolios decide in turn. Only an accepted decision reserves, so a rejected
   portfolio leaves its supply to the next one.
4. Define `order`, which takes what `prepare_portfolio` returned for each portfolio, by id, the shared state and the
   params, and returns the ids in the order they decide. Who gets a scarce bond is a client-fairness decision, so state
   the rule in the model's `README.md`. With the cvxpy example's `Portfolio`, which has the portfolio's `cash`:

   ```python
   def order(prepared: Mapping[str, Portfolio], shared: Market, params: Params) -> list[str]:
       """Largest cash first, then by id."""
       return sorted(prepared, key=lambda portfolio_id: (-prepared[portfolio_id].cash, portfolio_id))
   ```

   It must return every id once, or the run fails. Without `order`, portfolios decide in the run's order: the order of
   `--portfolios`, or of the holdings file with `--data`.
5. Test the sequence in a behaviour test: call `reserve` on a decision, such as `optim.Decision(buy={"NEW": 1000.0})`,
   and assert on what it leaves, and call `order` on a few prepared portfolios.

`prepare_portfolio` still runs for every portfolio in parallel, before any decides; only `decide`, `check` and
`reserve` run one at a time, in the run's own process, so a run of many portfolios takes about the sum of their
decisions.

To confirm it worked, run the model on its fixture:

```sh
uv run --env-file .env optim run MODEL --params params/MODEL/default.json --data tests/MODEL/fixture --as-of 2026-10-16
```

`snapshots/RUN_ID/run.json` records the order under `order`, and `snapshots/RUN_ID/trades.parquet` should show the
first portfolios buying the supply and the later ones what is left. Replaying one portfolio of such a run decides the
portfolios before it again, so it sees the supply it saw:

```sh
uv run --env-file .env optim replay RUN_ID --portfolios PORTFOLIO_ID
```

If the allocation rule does not depend on what each portfolio decides, such as pro rata by size, allocate the supply in
`prepare` instead and keep `decide` parallel. For the trade-off, see
[how optim is put together](../explanation.md#portfolios-that-compete-for-supply).
