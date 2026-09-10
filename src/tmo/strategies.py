"""The pre-registered strategies. Rules live in the spec, not here.

Every function is pure: given the spec, the replayed book and today's marks it
returns the fills to make. No randomness, no clock reads, no network. Two runs
on the same inputs produce the same trades, which is what makes the ledger
checkable by a stranger.

Fills are deliberately unkind to us: we sell at the bid and buy at the ask, and
pay taker fees on both. Only two-sided quotes are tradeable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .portfolio import Book, make_trade


def _tradeable(marks: pd.DataFrame) -> pd.DataFrame:
    return marks[marks["two_sided"] & marks["bid_usd"].gt(0) & marks["ask_usd"].gt(0)
                 & marks["ref_iv"].notna()]


def _fill_price(row: pd.Series, qty: float) -> float:
    return float(row["ask_usd"] if qty > 0 else row["bid_usd"])


def _hedge(spec: dict, book: Book, marks: pd.DataFrame, option_delta: float,
           expiry_label: str, forward: float) -> list[dict]:
    """Trade the forward back to the spec's target delta, inside a band."""
    h = spec["hedging"]
    if h.get("instrument") in (None, "none"):
        return []
    target = float(h.get("target_delta", 0.0)) - option_delta
    name = f"BTC-FWD-{expiry_label}"
    current = sum(v["qty"] for k, v in book.positions.items()
                  if v["kind"] == "future" and k == name)
    diff = target - current
    if abs(diff) < float(h.get("band_btc", 0.05)):
        return []
    return [make_trade(instrument=name, kind="future", qty=diff, price_usd=forward,
                       forward=forward, action="hedge",
                       reason=f"delta {option_delta:+.4f} -> target {target:+.4f}")]


def _book_option_delta(book: Book, marks: pd.DataFrame) -> float:
    by_inst = marks.set_index("instrument")
    d = 0.0
    for name, p in book.open_options.items():
        if name in by_inst.index and np.isfinite(by_inst.loc[name, "delta"]):
            d += p["qty"] * float(by_inst.loc[name, "delta"])
    return d


def btc_weekly_vrp(spec: dict, book: Book, marks: pd.DataFrame) -> tuple[list[dict], str]:
    """Short the front-week at-the-money straddle, delta-hedged daily.

    The plainest possible expression of the variance risk premium. It is here
    as the reference case: if a correct surface is worth anything, a strategy
    that ignores the surface except for hedging is the thing it has to beat.
    """
    q = _tradeable(marks)
    trades: list[dict] = []
    open_opts = book.open_options

    # -- exit ---------------------------------------------------------------
    close_below = float(spec["exit"]["close_when_dte_below"])
    by_inst = marks.set_index("instrument")
    to_close = []
    for name, p in open_opts.items():
        dte = float(by_inst.loc[name, "dte"]) if name in by_inst.index else -1.0
        if dte < close_below:
            to_close.append(name)
    if to_close:
        for name in to_close:
            p = open_opts[name]
            qty = -p["qty"]
            if name in set(q["instrument"]):
                row = q.set_index("instrument").loc[name]
                px, imputed = _fill_price(row, qty), False
                fwd = float(row["forward"])
            else:
                fwd = float(marks.sort_values("T")["forward"].iloc[0])
                px = max(0.0, (fwd - p["strike"]) if p["is_call"] else (p["strike"] - fwd))
                imputed = True
            trades.append(make_trade(instrument=name, kind="option", qty=qty, price_usd=px,
                                     forward=fwd, action="close",
                                     reason="dte below exit threshold" if not imputed
                                     else "instrument gone from chain; settled at intrinsic",
                                     strike=p["strike"], expiry=p["expiry"],
                                     is_call=p["is_call"], imputed=imputed))
        for name, p in book.positions.items():
            if p["kind"] == "future" and abs(p["qty"]) > 1e-12:
                fwd = float(marks.sort_values("T")["forward"].iloc[0])
                trades.append(make_trade(instrument=name, kind="future", qty=-p["qty"],
                                         price_usd=fwd, forward=fwd, action="close",
                                         reason="unwind hedge with the straddle"))
        return trades, f"closed {len(to_close)} legs"

    # -- entry --------------------------------------------------------------
    if not open_opts:
        lo, hi = spec["entry"]["dte_window"]
        target = float(spec["entry"]["target_dte"])
        cand = q[(q["dte"] >= lo) & (q["dte"] <= hi)]
        if cand.empty:
            return [], f"no expiry with dte in [{lo}, {hi}]"
        expiries = cand["expiry"].unique()
        pick = min(expiries, key=lambda e: abs(float(cand[cand["expiry"] == e]["dte"].iloc[0]) - target))
        sl = cand[cand["expiry"] == pick]
        fwd = float(sl["forward"].median())
        strike = float(sl.iloc[(sl["strike"] - fwd).abs().argsort().iloc[0]]["strike"])
        legs = sl[sl["strike"] == strike]
        if set(legs["is_call"]) != {True, False}:
            return [], f"strike {strike:.0f} is not two-sided on both legs"
        n = float(spec["sizing"]["contracts_per_leg"])
        for _, row in legs.iterrows():
            trades.append(make_trade(instrument=row["instrument"], kind="option", qty=-n,
                                     price_usd=_fill_price(row, -n), forward=fwd,
                                     action="open", reason="front-week ATM straddle, sold",
                                     strike=float(row["strike"]),
                                     expiry=str(row["expiry"]), is_call=bool(row["is_call"])))
        # hedge from the position we are about to hold, not the empty book
        delta = float((legs["delta"] * -n).sum())
        label = pd.Timestamp(pick).strftime("%Y%m%d")
        trades += _hedge(spec, book, marks, delta, label, fwd)
        return trades, f"opened straddle {strike:.0f} expiring {label}"

    # -- daily hedge --------------------------------------------------------
    any_opt = next(iter(open_opts.values()))
    label = pd.Timestamp(any_opt["expiry"]).strftime("%Y%m%d")
    sl = marks[marks["expiry"].astype(str) == str(any_opt["expiry"])]
    fwd = float(sl["forward"].median()) if len(sl) else float(marks.sort_values("T")["forward"].iloc[0])
    trades += _hedge(spec, book, marks, _book_option_delta(book, marks), label, fwd)
    return trades, "held; hedged" if trades else "held; inside the delta band"


def btc_hold_context(spec: dict, book: Book, marks: pd.DataFrame) -> tuple[list[dict], str]:
    """Hold one BTC. Not a benchmark -- context.

    A delta-hedged vol strategy is not trying to beat long BTC and comparing
    them directly would be dishonest. This line exists so a reader can see what
    the market did over the same window without having to go and look.
    """
    if book.positions:
        return [], "held"
    fwd = float(marks.sort_values("T")["forward"].iloc[0])
    n = float(spec["sizing"]["btc"])
    return [make_trade(instrument="BTC-FWD-CONTEXT", kind="future", qty=n, price_usd=fwd,
                       forward=fwd, action="open",
                       reason="one-off purchase, never traded again")], "opened"


REGISTRY = {
    "btc-weekly-vrp": btc_weekly_vrp,
    "btc-hold-context": btc_hold_context,
}
