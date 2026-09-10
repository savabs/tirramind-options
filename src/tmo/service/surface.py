"""Shape one fitted chain into the payload the terminal renders.

Every response carries its own error. That is the product: a surface without
its residuals is a picture, and the category is already full of pictures whose
footnote says the numbers are estimates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .. import market


def _expiry_labels(marks: pd.DataFrame) -> dict[float, str]:
    """Map each fitted maturity to the date a human would name it by."""
    out: dict[float, str] = {}
    for T, grp in marks.groupby("T"):
        out[float(T)] = pd.Timestamp(grp["expiry"].iloc[0]).strftime("%Y-%m-%d")
    return out


def _nearest(labels: dict[float, str], T: float) -> str:
    if not labels:
        return f"{T * 365:.2f}d"
    key = min(labels, key=lambda k: abs(k - T))
    return labels[key]


def build(currency: str, *, venue: str = "deribit") -> dict[str, Any]:
    """Fetch, fit and shape. Costs about eight seconds; call it off the hot path."""
    marks, report, meta = market.state(currency, capture=False)
    labels = _expiry_labels(marks)
    spot = float(marks.sort_values("T")["forward"].iloc[0])
    expiries = []
    for sl in report.slices:
        T = float(sl["T"])
        expiries.append({
            "expiry": _nearest(labels, T),
            "dte": round(T * 365.0, 3),
            "forward": round(float(sl["forward"]), 2),
            "quotes": int(sl["n"]),
            "rmse_vol_pts": round(float(sl["refined_rmse_vol_pts"]), 4),
            "inside_bid_ask": round(float(sl["refined_inside_bid_ask"]), 4),
            "status": sl["refined_status"],
            "strike": sl["strike"],
            "log_moneyness": sl["k"],
            "bid_iv": sl["bid_iv"],
            "ask_iv": sl["ask_iv"],
            "venue_mark_iv": sl["mark_iv"],
            "our_iv": sl["ref_iv"],
        })
    return {
        "venue": venue,
        "currency": currency.upper(),
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "forward_front": round(spot, 2),
        "quality": {
            "rmse_vol_pts": round(float(meta["refined_rmse_vol_pts"]), 4),
            "inside_bid_ask": round(float(meta["refined_inside_bid_ask"]), 4),
            "quotes_fitted": int(meta["n_fit"]),
            "expiries": int(meta["expiries"]),
            "our_butterfly_violations": int(meta["our_butterfly_violations"]),
            "our_calendar_violations": int(meta["our_calendar_violations"]),
            "executable_venue_arbs": int(meta["executable_venue_arbs"]),
            "guarantee": report.refined["guarantee"],
        },
        "expiries": expiries,
    }


def summarise(payload: dict[str, Any]) -> dict[str, Any]:
    """The same payload without the per-strike arrays, for an index view."""
    out = {k: v for k, v in payload.items() if k != "expiries"}
    out["expiries"] = [{k: v for k, v in e.items()
                        if k not in ("strike", "log_moneyness", "bid_iv", "ask_iv",
                                     "venue_mark_iv", "our_iv")}
                       for e in payload["expiries"]]
    return out


__all__ = ["build", "summarise"]
