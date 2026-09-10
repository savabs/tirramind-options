"""A synthetic BTC chain. No network, no voltorch, fully deterministic."""

from __future__ import annotations

import numpy as np
import pandas as pd


def chain(*, dtes=(3.0, 7.0, 30.0), forward=60000.0, strikes=None, iv=0.55) -> pd.DataFrame:
    strikes = strikes or [55000.0, 60000.0, 65000.0]
    rows = []
    base = pd.Timestamp("2026-09-10", tz="UTC")
    for dte in dtes:
        expiry = base + pd.Timedelta(days=dte)
        for K in strikes:
            for is_call in (True, False):
                # A crude but monotone price so tests can reason about signs.
                intrinsic = max(0.0, (forward - K) if is_call else (K - forward))
                time_value = forward * iv * np.sqrt(dte / 365.0) * 0.4
                mid = intrinsic + time_value
                rows.append({
                    "instrument": f"BTC-{expiry:%d%b%y}-{K:.0f}-{'C' if is_call else 'P'}".upper(),
                    "expiry": expiry, "T": dte / 365.0, "dte": dte, "strike": K,
                    "is_call": is_call, "forward": forward,
                    "bid_usd": mid * 0.98, "ask_usd": mid * 1.02, "mid_usd": mid,
                    "two_sided": True, "mark_iv": iv, "ref_iv": iv, "our_usd": mid,
                    "delta": 0.5 if is_call else -0.5, "gamma": 1e-5,
                    "vega": forward * 0.004, "theta": -forward * 0.5,
                })
    return pd.DataFrame(rows)
