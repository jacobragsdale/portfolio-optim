# cvxpy example: a taxable SMA rebalance

Rebalances each separately managed fixed income portfolio on its own, as an integer program solved through
`optim.cvxpy`. "Taxable" describes the mandates: the model trades holdings by security, and neither selects tax lots
nor harvests losses.

## What it maximizes

Expected income over a year after trading, net of estimated trading costs, as a fraction of the portfolio's value.
Each bond's expected return comes from the signals API; a bond without a signal is not bought.

## Constraints

- Trades are sizes the market trades: a buy is whole increments of at least the minimum piece, and a sale is the whole
  holding, or whole increments of at least the minimum piece that leave at least the minimum remaining.
- No bond is more than `max_weight` of the portfolio's value after trading, unless the portfolio may not sell it, and
  no issuer more than `max_issuer_weight`.
- Buys plus sales are at most `max_turnover` of the portfolio's value.
- Cash after trading and costs is not negative.
- The portfolio's controls bind whatever the params say: `no_buy`, `no_sell`, `hold`, and `must_sell`, which sells the
  whole holding and buys none back. Controls that both require and forbid a sale reject the portfolio.
- Rules from the params: buy only bonds rated `min_rating` or better, and never the issuers in `exclude_issuers`.

## Data

The shared holdings and securities, the expected returns of the signals API (`signals`), and each portfolio's trading
controls from SQL Server (`controls`).

## Known limits

- One currency per portfolio, and no FX rates.
- The trading cost is one flat estimate in basis points, the same for every bond.
- Portfolios do not compete for a bond's supply: each decides alone.
