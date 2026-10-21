"""A portfolio's controls applied to its lines: prep, not a rule, so they bind whatever the strategy, and a control
that cannot be applied fails the portfolio instead of being skipped. A new kind of control is an `Action` and its
handling in `apply_controls`."""

import logging

import numpy as np
import pandas as pd

from optim.prep import Portfolio

log = logging.getLogger(__name__)


def apply_controls(lines: pd.DataFrame, portfolio: Portfolio, known: pd.Index) -> pd.DataFrame:
    """`lines` with the portfolio's controls applied. A control that names an unknown security fails the portfolio, as
    do controls that disagree: each is how a control silently stops applying. A mandatory sale of a security the
    portfolio no longer holds has happened, as in a working run after its trade; it still stops the security being
    bought back."""
    portfolio_id, controls = portfolio.details.portfolio_id, portfolio.controls
    named = controls["security_id"].dropna()
    if (unknown := known.get_indexer(pd.Index(named)) < 0).any():
        msg = f"portfolio {portfolio_id}: controls name securities that are not loaded, e.g. {named[unknown].head(3).tolist()}; a control that matches nothing would not apply"
        raise LookupError(msg)

    def applies(*actions: str) -> np.ndarray:
        these = controls[controls["action"].isin(actions)]
        return lines["security_id"].isin(these["security_id"].dropna()).to_numpy() | bool(these["security_id"].isna().any())

    held = lines["sellable"].to_numpy()  # every held line, before any control
    must, blocked = applies("must_sell") & held, applies("no_sell", "hold") & held
    if conflict := sorted(set(lines.loc[must & blocked, "security_id"])):
        involved = controls[controls["security_id"].isin(conflict) | controls["security_id"].isna()]
        msg = f"portfolio {portfolio_id}: controls both require and forbid selling {conflict[:5]}: {_quote(involved)}"
        raise ValueError(msg)
    mandatory = controls.loc[controls["action"].eq("must_sell") & controls["security_id"].notna(), "security_id"]
    if not (unheld := mandatory[~mandatory.isin(lines.loc[held, "security_id"])]).empty:
        log.warning(
            "%s: mandatory sales of securities it no longer holds are done", portfolio_id, extra={"portfolio_id": portfolio_id, "controls": _quote(controls[controls["security_id"].isin(unheld)])}
        )
    return lines.assign(buyable=lines["buyable"] & ~applies("no_buy", "hold", "must_sell"), sellable=held & ~blocked, must_sell=must)


def _quote(controls: pd.DataFrame) -> str:
    return "; ".join(f"{c.control_id} {c.action} {c.security_id if isinstance(c.security_id, str) else 'every security'} ({c.reason})" for c in controls.itertuples())
