# How to write a rule

Add one of the model's rules: logic that narrows what a portfolio may buy or sell, or adjusts what the decision
believes, such as a rating floor, a restricted list, or a client's no-sell list. Rules are much of a model's particular
logic, so each is a plain function in the model's `rules.py`, tested without a solver.

Prerequisites:

- A model with a `prepare_portfolio(portfolio, shared, params)` hook, as the cvxpy example has, which returns what
  `decide` and `check` receive as `prepared`. A model without one passes `decide` the portfolio's slice of `Data`; add
  the hook first (see the [hooks reference](../reference.md#the-model-module)).
- Any data the rule reads, as a dataset of the model (see [how to add a dataset](add-a-dataset.md)).

To write a rule:

1. Write it in `rules.py` as a function of frames and values that returns a frame. Narrow flags; never drop rows. A
   security the portfolio may not buy may still be held, and must still be sellable:

   ```python
   def exclude_sectors(lines: pd.DataFrame, sectors: list[str]) -> pd.DataFrame:
       """Never buy these sectors."""
       return lines.assign(buyable=lines["buyable"] & ~lines["sector"].isin(sectors))
   ```

   The cvxpy example's `prepare_portfolio` builds `lines`, one row per security the portfolio holds or may buy, with
   every column of the shared securities (`issuer`, `sector`, `rating`, `maturity` and the rest), `unit_value`,
   `expected_return`, `held`, and the flags `buyable`, `sellable` and `must_sell`, which its `decide` reads.
2. If a param drives the rule, add it to `Params`, with a docstring under it and a default, and to
   `params/MODEL/default.json`.
3. Call the rule in `prepare_portfolio`, where `lines` and `params` are already in hand, in the order the rules
   apply:

   ```python
   lines = rules.exclude_sectors(lines, params.exclude_sectors)
   ```

   What every portfolio must obey whatever the params, such as a portfolio's mandatory controls, belongs here too,
   applied before the rules a param chooses, so that no params file can leave it out.
4. Test it in `tests/MODEL/test_rules.py`, on a few rows built with `optim.testing.frame`, asserting on the frame it
   returns. `frame` fills each row's missing fields from a valid default row: for securities, bond `A` in the
   `Corporate` sector; for holdings, P1's $1mm of cash, which `{}` stands for:

   ```python
   from optim.datasets import HOLDINGS, SECURITIES
   from optim.testing import CASH, frame

   from ladder.ladder import rules
   from ladder.ladder.datasets import SIGNALS

   MARKET = rules.market(frame(SECURITIES, CASH, {"security_id": "A"}), frame(SIGNALS, {"security_id": "A", "expected_return": 0.05}))


   def test_excluded_sectors_may_not_be_bought_but_may_be_sold() -> None:
       held = rules.lines(frame(HOLDINGS, {}, {"security_id": "A", "quantity": 5000.0}), MARKET)

       excluded = rules.exclude_sectors(held, ["Corporate"]).set_index("security_id")

       assert not excluded.loc["A", "buyable"]
       assert excluded.loc["A", "sellable"]
   ```

5. Name the rule, in business terms, under "Constraints" in the model's `README.md`.
6. If the rule is a limit the rounded trades could break, add a reason for it to the model's
   `check(prepared, decision)`, which returns a list of strings, one per reason to reject. A rule that only narrows
   `buyable` or `sellable` needs none: the cvxpy example's `check` already rejects a trade its flags forbid.

To confirm it worked, run `uv run pytest`, then replay a saved run with a params file that sets the new param, such
as `"exclude_sectors": ["Corporate"]`, which leaves the fixture's portfolios nothing to buy:

```sh
uv run --env-file .env optim replay RUN_ID --params FILE
```

Compare the replay's `trades.parquet` with the run's, both under `snapshots/`. A replay works only while the model's
datasets are unchanged.

A rule that needs the whole market at once, such as ranking every bond, belongs in `prepare`, which runs once per run;
a rule about one portfolio belongs in `prepare_portfolio`, which runs in parallel. For why the hooks are split so, see
[how optim is put together](../explanation.md#a-model-in-three-hooks-and-four-more).
