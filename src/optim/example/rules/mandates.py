"""The portfolio's mandates, as constraints on top of the params' own."""

from dataclasses import replace

from optim.example import model


def constraints(portfolio: model.Portfolio, market: model.Market, params: model.Params) -> model.Portfolio:
    """Each of the portfolio's sector caps, as a weight constraint on the sector, added to the params' constraints:
    both apply, so the tighter binds."""
    caps = [{"kind": "weight", "where": {"sector": row.sector}, "max": row.max_weight} for row in portfolio.data.mandates.itertuples()]
    return replace(portfolio, constraints=[*portfolio.constraints, *caps])
