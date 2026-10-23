# plain example: a model-portfolio rebalance

Trades every portfolio toward one model portfolio, in plain Python with no solver.

## What it does

Each security in `targets` is bought or sold toward its target weight of the portfolio's value, unless it is already
within `tolerance` of it. Every other holding but cash is sold whole. What the targets leave is cash. The core rounds
each trade to its security's increment, sells an odd lot whole, and rejects a decision that would overdraw cash.

## Constraints

None of its own: the targets are the model. A portfolio that cannot afford its targets after rounding is rejected by
the core's cash check.

## Data

The shared holdings and securities alone.

## Known limits

- One model portfolio for every portfolio: targets that differ by portfolio need a dataset of them.
- No trading costs, minimum pieces or limits: a portfolio far from its targets trades in one go.
