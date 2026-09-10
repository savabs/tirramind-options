"""Positions and cash, reconstructed by replaying the trade ledger.

There is no state file. The ledger is the state, so the published record and
the thing the strategies act on are the same object and cannot drift apart.
Replaying a few thousand rows costs milliseconds and buys that guarantee.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np

# Deribit taker fees, published: options are 0.03% of the underlying, capped at
# 12.5% of the option's own premium (the cap is what makes selling cheap wings
# viable at all); futures are 0.05% of notional. Every paper fill in this
# ledger pays the taker fee and crosses the spread. A track record that fills
# at mid is a track record of nothing.
OPTION_FEE_RATE = 0.0003
OPTION_FEE_CAP = 0.125
FUTURE_FEE_RATE = 0.0005


def option_fee(forward: float, price_usd: float, qty: float) -> float:
    return float(abs(qty) * min(OPTION_FEE_RATE * forward, OPTION_FEE_CAP * abs(price_usd)))


def future_fee(price_usd: float, qty: float) -> float:
    return float(abs(qty) * price_usd * FUTURE_FEE_RATE)


def trade_id(strategy_id: str, as_of: str, seq: int, t: dict) -> str:
    """Content-derived, so an exact replay of the same day is a no-op append.

    It is not a guard against a *different* second run on the same day; the
    day-level guard in ``daily`` does that.
    """
    blob = json.dumps({"s": strategy_id, "d": as_of, "n": seq, "i": t["instrument"],
                       "q": t["qty"], "p": round(float(t["price_usd"]), 6),
                       "a": t["action"]}, sort_keys=True)
    return f"{strategy_id}|{as_of}|{seq}|{hashlib.sha256(blob.encode()).hexdigest()[:8]}"


@dataclass
class Book:
    cash_usd: float = 0.0
    # instrument -> {"qty", "kind", "strike", "expiry", "is_call"}
    positions: dict = field(default_factory=dict)

    @property
    def open_options(self) -> dict:
        return {k: v for k, v in self.positions.items()
                if v["kind"] == "option" and abs(v["qty"]) > 1e-12}

    @property
    def net_future(self) -> float:
        return sum(v["qty"] for v in self.positions.values() if v["kind"] == "future")


def replay(trades: list[dict]) -> Book:
    book = Book()
    for t in sorted(trades, key=lambda r: (r["as_of"], r["seq"])):
        book.cash_usd += float(t["cash_delta_usd"])
        p = book.positions.setdefault(t["instrument"], {
            "qty": 0.0, "kind": t["kind"], "strike": t.get("strike"),
            "expiry": t.get("expiry"), "is_call": t.get("is_call")})
        p["qty"] += float(t["qty"])
        if abs(p["qty"]) < 1e-12:
            del book.positions[t["instrument"]]
    return book


def make_trade(*, instrument: str, kind: str, qty: float, price_usd: float,
               forward: float, action: str, reason: str, strike=None, expiry=None,
               is_call=None, imputed: bool = False) -> dict:
    """One fill. ``qty`` is signed: positive buys, negative sells."""
    fee = (option_fee(forward, price_usd, qty) if kind == "option"
           else future_fee(price_usd, qty))
    return {"instrument": instrument, "kind": kind, "qty": float(qty),
            "price_usd": float(price_usd), "fee_usd": fee,
            "cash_delta_usd": float(-qty * price_usd - fee),
            "action": action, "reason": reason, "strike": strike,
            "expiry": expiry, "is_call": is_call, "imputed": imputed}


def value(book: Book, marks, *, price_col: str) -> dict:
    """Mark the book. Returns equity and aggregate greeks.

    A held instrument absent from the chain is valued at intrinsic against the
    shortest-dated forward and flagged, which happens only when a run was
    missed for long enough that the option expired unobserved.
    """
    by_inst = marks.set_index("instrument")
    spot = float(marks.sort_values("T")["forward"].iloc[0])
    pos_value = 0.0
    greeks = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    missing = []
    for name, p in book.positions.items():
        if p["kind"] == "future":
            # A forward position is worth the change in the forward; entry cash
            # already left the book at the entry price, so mark it at the
            # current shortest-dated forward.
            pos_value += p["qty"] * spot
            greeks["delta"] += p["qty"]
            continue
        if name not in by_inst.index:
            missing.append(name)
            intrinsic = max(0.0, (spot - p["strike"]) if p["is_call"] else (p["strike"] - spot))
            pos_value += p["qty"] * intrinsic
            continue
        row = by_inst.loc[name]
        px = float(row[price_col])
        if not np.isfinite(px):
            px = float(row["our_usd"])
        pos_value += p["qty"] * px
        for g in greeks:
            v = float(row[g]) if np.isfinite(row[g]) else 0.0
            greeks[g] += p["qty"] * v
    return {"cash_usd": book.cash_usd, "position_value_usd": pos_value,
            "equity_usd": book.cash_usd + pos_value, "missing_instruments": missing,
            **{f"greek_{k}": v for k, v in greeks.items()}}
