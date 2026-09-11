"""The markets we fit, and how each one quotes.

Two conventions live here and confusing them is the fastest way to publish a
wrong surface.

**Inverse**, which is Deribit's BTC and ETH book: the option is quoted in the
coin, so a price of 0.03 BTC is 0.03 times the forward in dollars. voltorch
verified that to 1e-4 against the venue's own marks and its loader does it.

**Linear**, which is Deribit's USDC book: the option is quoted in USDC, so the
price is already in dollars and multiplying by the forward would overstate it by
the price of the underlying. Everything else is the same Black-76 on the forward
with r = 0.

Getting that backwards does not raise; it produces a surface that looks
plausible and is wrong by orders of magnitude, which is why
``verify_convention`` exists and is a test rather than a comment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import torch
from voltorch import implied_volatility_bisect
from voltorch.deribit import fetch_chain as fetch_inverse

API = "https://www.deribit.com/api/v2/public"
UA = "tirramind-options/0.1 (+https://github.com/savabs/tirramind-options)"

COLUMNS = ["instrument", "expiry", "T", "strike", "is_call", "forward", "mark_iv",
           "bid", "ask", "bid_usd", "ask_usd", "two_sided", "bid_iv", "ask_iv",
           "open_interest", "volume"]


@dataclass(frozen=True)
class Market:
    """One tradeable surface: what to call it, where it lives, how it quotes."""

    key: str            # what the product calls it
    venue: str
    base: str           # the underlying
    convention: str     # "inverse" or "linear"
    settled_in: str

    @property
    def label(self) -> str:
        return f"{self.base} · {self.settled_in}"


MARKETS: tuple[Market, ...] = (
    Market("BTC", "deribit", "BTC", "inverse", "BTC"),
    Market("ETH", "deribit", "ETH", "inverse", "ETH"),
    Market("SOL", "deribit", "SOL", "linear", "USDC"),
    Market("XRP", "deribit", "XRP", "linear", "USDC"),
    Market("HYPE", "deribit", "HYPE", "linear", "USDC"),
    Market("TRX", "deribit", "TRX", "linear", "USDC"),
    Market("AVAX", "deribit", "AVAX", "linear", "USDC"),
)

BY_KEY = {m.key: m for m in MARKETS}


def _get(path: str, session: requests.Session, **params) -> list:
    r = session.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


def fetch_linear(base: str, *, session: requests.Session | None = None,
                 now: datetime | None = None, min_T_days: float = 1.0) -> pd.DataFrame:
    """Deribit's USDC book for one underlying, in the shape ``fit_chain`` wants.

    The prices are already in dollars. The only work is filtering the shared
    USDC book down to one underlying and solving for the implied volatilities,
    which are ours rather than the venue's.
    """
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", UA)
    now = now or datetime.now(timezone.utc)

    inst = {i["instrument_name"]: i for i in
            _get("get_instruments", s, currency="USDC", kind="option", expired="false")
            if i.get("base_currency") == base}
    if not inst:
        return pd.DataFrame(columns=COLUMNS)
    time.sleep(0.2)
    book = _get("get_book_summary_by_currency", s, currency="USDC", kind="option")

    rows = []
    for b in book:
        i = inst.get(b["instrument_name"])
        if not i:
            continue
        exp = datetime.fromtimestamp(i["expiration_timestamp"] / 1000, tz=timezone.utc)
        T = (exp - now).total_seconds() / (365.0 * 86400)
        if T * 365 < min_T_days:
            continue
        bid, ask = b.get("bid_price"), b.get("ask_price")
        rows.append({
            "instrument": b["instrument_name"], "expiry": exp, "T": T,
            "strike": float(i["strike"]), "is_call": i["option_type"] == "call",
            "forward": float(b["underlying_price"]),
            "mark_iv": (b.get("mark_iv") or np.nan) / 100.0,
            "bid": bid if bid is not None else np.nan,
            "ask": ask if ask is not None else np.nan,
            "open_interest": b.get("open_interest") or 0.0,
            "volume": b.get("volume") or 0.0,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    # Linear: the quote is already in dollars. Multiplying by the forward here,
    # which is what the inverse book needs, would overstate every price by the
    # price of the underlying.
    df["bid_usd"] = df["bid"]
    df["ask_usd"] = df["ask"]
    df["two_sided"] = (df["bid"].notna() & df["ask"].notna()
                       & (df["bid"] > 0) & (df["ask"] > 0))
    df["bid_iv"], df["ask_iv"] = np.nan, np.nan
    m = df["two_sided"].values
    if m.any():
        F = torch.tensor(df.loc[m, "forward"].values, dtype=torch.float64)
        K = torch.tensor(df.loc[m, "strike"].values, dtype=torch.float64)
        T = torch.tensor(df.loc[m, "T"].values, dtype=torch.float64)
        r = torch.zeros_like(F)
        for col, src in (("bid_iv", "bid_usd"), ("ask_iv", "ask_usd")):
            P = torch.tensor(df.loc[m, src].values, dtype=torch.float64)
            ivs = np.full(int(m.sum()), np.nan)
            for is_call in (True, False):
                cm = torch.tensor(df.loc[m, "is_call"].values == is_call)
                if cm.any():
                    iv = implied_volatility_bisect(F[cm], K[cm], T[cm], r[cm], P[cm],
                                                   is_call=is_call)
                    ivs[cm.numpy()] = iv.detach().numpy()
            df.loc[m, col] = ivs
    return df[COLUMNS].sort_values(["expiry", "strike", "is_call"]).reset_index(drop=True)


def fetch(market: Market | str) -> pd.DataFrame:
    m = BY_KEY[market] if isinstance(market, str) else market
    return fetch_inverse(m.base) if m.convention == "inverse" else fetch_linear(m.base)


def verify_convention(df: pd.DataFrame, *, sample: int = 60) -> dict:
    """Does our price convention agree with the venue's own marks?

    Solve for the volatility implied by the mid of the quoted spread, then ask
    what the venue says its own mark volatility is. If the convention is wrong
    the two disagree wildly rather than subtly, so this is a sharp test and not a
    tuning knob.

    Returns the median and worst absolute difference in volatility points, over
    quotes near the money where the spread is informative.
    """
    q = df[df["two_sided"] & df["mark_iv"].notna()
           & df["bid_iv"].notna() & df["ask_iv"].notna()].copy()
    if q.empty:
        return {"n": 0, "median_vol_pts": float("nan"), "worst_vol_pts": float("nan")}
    q["k"] = np.log(q["strike"] / q["forward"])
    # Nearest the money, not the most negative log-moneyness. The first version
    # of this took `nsmallest(k)`, which sampled the deepest puts, where the
    # spread is widest and the mid says least.
    q["abs_k"] = q["k"].abs()
    q = q[q["abs_k"] < 0.25].nsmallest(sample, "abs_k", keep="all")
    if q.empty:
        return {"n": 0, "median_vol_pts": float("nan"), "worst_vol_pts": float("nan")}
    mid = 0.5 * (q["bid_iv"] + q["ask_iv"])
    err = (mid - q["mark_iv"]).abs() * 100
    # The median is the number that matters. A wrong convention is wrong by
    # orders of magnitude on every quote; a wide spread on one quote is not.
    return {"n": int(len(q)), "median_vol_pts": float(err.median()),
            "worst_vol_pts": float(err.max()),
            "p90_vol_pts": float(err.quantile(0.9))}


__all__ = ["Market", "MARKETS", "BY_KEY", "fetch", "fetch_linear", "verify_convention"]
