"""This model's rules: plain functions of frames, tested without a solver. `lines` builds what one portfolio's decision
sees; the portfolio's controls, then the params' rating floor and restricted issuers, narrow what it may buy and sell.
A rule never drops a line: a security that may not be bought may still be held, and sold. `violations` is the model's
own acceptance check."""

import numpy as np
import pandas as pd
from optim import Decision
from optim.datasets import unit_value
from optim.schema import Rating

TOLERANCE = 1e-6  # in weights of the portfolio's value, as the core's acceptance


def market(securities: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    """Every security with its `unit_value` and `expected_return`, 0 without a signal. A bond without a signal cannot
    be bought: there is no view on it."""
    joined = securities.merge(signals, on="security_id", how="left", validate="one_to_one")
    return joined.assign(unit_value=unit_value(joined), has_signal=joined["expected_return"].notna(), expected_return=joined["expected_return"].fillna(0.0))


def lines(holdings: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    """One line per security the portfolio holds, cash aside, or may buy: every bond with a signal. Each has its
    security's columns, `held`, and `buyable`, `sellable` and `must_sell`, which rules narrow."""
    if unknown := sorted(set(holdings["security_id"]) - set(securities["security_id"])):
        msg = f"holds securities that are not loaded: {unknown[:5]}"
        raise LookupError(msg)
    held = holdings.groupby("security_id")["quantity"].sum().rename("held").reset_index()
    candidates = securities[(securities["asset_class"] == "bond") & securities["has_signal"]][["security_id"]]
    ids = pd.concat([held[["security_id"]], candidates], ignore_index=True).drop_duplicates()
    rows = ids.merge(securities, on="security_id", how="left", validate="one_to_one").merge(held, on="security_id", how="left", validate="one_to_one")
    rows = rows[rows["asset_class"] != "cash"].reset_index(drop=True).assign(held=lambda frame: frame["held"].fillna(0.0))
    if (short := rows["held"] < 0).any():
        msg = f"holds negative quantities of securities that are not cash, e.g. {rows.loc[short, 'security_id'].head(3).tolist()}: short positions are not modelled"
        raise ValueError(msg)
    return rows.assign(buyable=(rows["asset_class"] == "bond") & rows["has_signal"], sellable=rows["held"] > 0, must_sell=False)


def cash(holdings: pd.DataFrame, securities: pd.DataFrame) -> float:
    """The value of the portfolio's cash holdings, which may be negative."""
    held = holdings.merge(securities, on="security_id", how="inner", validate="many_to_one")
    is_cash = held["asset_class"] == "cash"
    return float((held.loc[is_cash, "quantity"] * unit_value(held.loc[is_cash])).sum())


def apply_controls(lines: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    """The portfolio's mandatory controls, whatever the params. A control naming no security applies to every one.
    Controls that both require and forbid a sale fail the portfolio, quoting both: the model does not choose between
    them."""

    def applies(*actions: str) -> np.ndarray:
        these = controls[controls["action"].isin(actions)]
        return lines["security_id"].isin(these["security_id"].dropna()).to_numpy() | bool(these["security_id"].isna().any())

    held = lines["sellable"].to_numpy()
    must, blocked = applies("must_sell") & held, applies("no_sell", "hold") & held
    if conflict := sorted(set(lines.loc[must & blocked, "security_id"])):
        involved = controls[controls["security_id"].isin(conflict) | controls["security_id"].isna()]
        quoted = "; ".join(f"{c.control_id} {c.action} {c.security_id if isinstance(c.security_id, str) else 'every security'} ({c.reason})" for c in involved.itertuples())
        msg = f"controls both require and forbid selling {conflict[:5]}: {quoted}"
        raise ValueError(msg)
    return lines.assign(buyable=lines["buyable"] & ~applies("no_buy", "hold", "must_sell"), sellable=held & ~blocked, must_sell=must)


def min_rating(lines: pd.DataFrame, rating: Rating) -> pd.DataFrame:
    """Buy only securities rated `rating` or better; ratings are an ordered category, best first."""
    return lines.assign(buyable=lines["buyable"] & (lines["rating"] <= rating).fillna(value=False))


def exclude_issuers(lines: pd.DataFrame, issuers: list[str]) -> pd.DataFrame:
    """Never buy these issuers, as a restricted list says."""
    return lines.assign(buyable=lines["buyable"] & ~lines["issuer"].isin(issuers))


def violations(lines: pd.DataFrame, value: float, decision: Decision, *, max_weight: float, max_issuer_weight: float, max_turnover: float) -> list[str]:
    """Why the decision breaks this model's rules: trade sizes the market does not trade, a control, or a limit."""
    by_id = lines.set_index("security_id")
    outside = sorted((set(decision.buy) | set(decision.sell)) - set(by_id.index))
    bought = pd.Series(decision.buy, dtype=float).reindex(by_id.index, fill_value=0.0)
    sold = pd.Series(decision.sell, dtype=float).reindex(by_id.index, fill_value=0.0)
    increment, min_piece = by_id["increment"], by_id["min_piece"].fillna(by_id["increment"])
    whole = (sold > 0) & ((sold - by_id["held"]).abs() <= TOLERANCE)

    def steps(quantity: pd.Series) -> pd.Series:
        return (quantity / increment - (quantity / increment).round()).abs() <= TOLERANCE

    odd_buys = (bought > 0) & (~steps(bought) | (bought < min_piece - TOLERANCE))
    leaves_too_little = by_id["held"] - sold < by_id["min_remaining"].fillna(0.0) - TOLERANCE
    odd_sales = (sold > 0) & ~whole & (~steps(sold) | (sold < min_piece - TOLERANCE) | leaves_too_little)
    reasons = [f"trades {s}, which it neither holds nor may buy" for s in outside]
    reasons += [f"buys {s} in a size the market does not trade" for s in by_id.index[odd_buys]]
    reasons += [f"sells {s} in a size the market does not trade" for s in by_id.index[odd_sales]]
    reasons += [f"buys {s}, which it may not" for s in by_id.index[(bought > 0) & ~by_id["buyable"]]]
    reasons += [f"sells {s}, which it may not" for s in by_id.index[(sold > 0) & ~by_id["sellable"]]]
    reasons += [f"does not sell all of {s}, which a control says it must" for s in by_id.index[by_id["must_sell"] & ~whole]]
    weights = by_id["unit_value"] * (by_id["held"] + bought - sold) / value
    cap = np.maximum(max_weight, np.where(by_id["sellable"], 0.0, by_id["unit_value"] * by_id["held"] / value))  # what it may not sell, it may keep
    reasons += [f"holds {weights[s]:.2%} in {s}, over its limit of {cap[i]:.2%}" for i, s in enumerate(by_id.index) if weights[s] > cap[i] + TOLERANCE]
    issuers = weights.groupby(by_id["issuer"]).sum()
    reasons += [f"holds {weight:.2%} in issuer {issuer}, over {max_issuer_weight:.2%}" for issuer, weight in issuers.items() if weight > max_issuer_weight + TOLERANCE]
    if (turnover := float((by_id["unit_value"] * (bought + sold)).sum() / value)) > max_turnover + TOLERANCE:
        reasons.append(f"trades {turnover:.2%} of its value, over {max_turnover:.2%}")
    return reasons
