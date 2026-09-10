"""Why does the refined fit's error move, and why do slices fall back?

Published numbers moved more than they should between runs half an hour apart:
0.69 to 1.04 volatility points of error, and one to four of eleven expiries
falling back from the refined SVI slice to the eSSVI backbone because they
failed the calendar check. Either the optimiser is unstable or the check is.

This script answers three questions with measurements rather than argument:

  1. Is a refit of the *same* chain reproducible? If not, the optimiser is the
     story and nothing else here matters.
  2. When a slice fails the calendar check, by how much, and where? A violation
     of 1e-6 in total variance out in an extrapolated wing is not the same
     finding as a violation of 1e-3 between two quoted strikes.
  3. Would the slice still fail if the check were run only where both slices
     have quotes, or with a tolerance scaled to the bid-ask spread?

Run: PYTHONPATH=src python research/fit_stability.py
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time

import numpy as np
import pandas as pd
import torch
from voltorch import durrleman_g, fit_chain, otm
from voltorch.deribit import fetch_chain
from voltorch.ssvi import ESSVI, SVISlice

GRID = torch.linspace(-2.0, 2.0, 400, dtype=torch.float64)
GRID_NP = GRID.numpy()


# ── question 1: is a refit reproducible? ────────────────────────────────────
def determinism(df: pd.DataFrame, repeats: int = 3) -> dict:
    runs = []
    for _ in range(repeats):
        t0 = time.time()
        r = fit_chain(df, currency="BTC")
        runs.append({
            "rmse": r.refined["rmse_vol_pts"],
            "inside": r.refined["inside_bid_ask_share"],
            "fallbacks": sorted(r.refined["slices_fallback"]),
            "seconds": round(time.time() - t0, 2),
        })
    rmses = [r["rmse"] for r in runs]
    return {
        "repeats": repeats,
        "rmse_values": rmses,
        "rmse_spread": max(rmses) - min(rmses),
        "identical_fallbacks": all(r["fallbacks"] == runs[0]["fallbacks"] for r in runs),
        "runs": runs,
    }


# ── question 2 and 3: what the calendar check is actually rejecting ─────────
def _refit_slices(df: pd.DataFrame):
    """Rebuild the backbone and the refined slices exactly as fit_chain does."""
    q = otm(df).copy()
    q["k"] = np.log(q["strike"] / q["forward"])
    q["mid_iv"] = 0.5 * (q["bid_iv"] + q["ask_iv"])
    q["spread"] = (q["ask_iv"] - q["bid_iv"]).clip(lower=0.002)
    expiries = np.sort(q["T"].unique())

    theta0 = torch.tensor([
        float((q[q["T"] == t].assign(a=lambda d: d.k.abs()).nsmallest(3, "a")["mid_iv"] ** 2).mean() * t)
        for t in expiries], dtype=torch.float64)
    backbone = ESSVI(torch.tensor(expiries, dtype=torch.float64), theta_init=theta0)
    backbone.fit(torch.tensor(q["k"].values), torch.tensor(q["T"].values),
                 torch.tensor(q["mid_iv"].values),
                 torch.tensor(1.0 / q["spread"].values ** 2), adam_steps=300, lbfgs_steps=60)

    out = []
    for t in expiries:
        s = q[q["T"] == t]
        kk = torch.tensor(s["k"].values)
        ivt = torch.tensor(s["mid_iv"].values)
        ww = torch.tensor(1.0 / s["spread"].values ** 2)
        span = float(max(abs(s["k"].min()), abs(s["k"].max()), 0.05))
        warm = torch.linspace(-1.5 * span, 1.5 * span, 80, dtype=torch.float64)
        pen = torch.linspace(-2.0 * span, 2.0 * span, 80, dtype=torch.float64)
        chk_lo, chk_hi = max(-2.0, -3.0 * span), min(2.0, 3.0 * span)
        cands = []
        for sl in (SVISlice.from_backbone(backbone, float(t), warm),
                   SVISlice.from_quotes(float(t), kk, ivt)):
            sl.fit(kk, ivt, ww, steps=600, lr=0.02, g_grid=pen)
            with torch.no_grad():
                cands.append((float(((sl.implied_vol(kk) - ivt) ** 2 * ww).mean()), sl))
        sl = min(cands, key=lambda c: c[0])[1]
        with torch.no_grad():
            w_ref = sl.total_variance(GRID).numpy()
            w_bb = backbone.total_variance(GRID, torch.full_like(GRID, float(t))).numpy()
        g = durrleman_g(torch.linspace(chk_lo, chk_hi, 300, dtype=torch.float64), sl.total_variance)
        # Spread expressed in total variance, so a violation can be compared
        # against what the market itself leaves undetermined.
        spread_w = float((2 * s["mid_iv"] * s["spread"] * float(t)).median())
        out.append({
            "T": float(t), "dte": float(t) * 365, "n": int(len(s)),
            "k_lo": float(s["k"].min()), "k_hi": float(s["k"].max()),
            "validated": (chk_lo, chk_hi), "span": span,
            "butterfly_ok": bool(float(g.min()) >= -1e-9),
            "w_refined": w_ref, "w_backbone": w_bb, "spread_w": spread_w,
        })
    return out


def _rmse_under(df: pd.DataFrame, slices: list[dict], w_key: str) -> dict:
    """Error of the surface a given acceptance rule actually publishes."""
    q = otm(df).copy()
    q["k"] = np.log(q["strike"] / q["forward"])
    q["mid_iv"] = 0.5 * (q["bid_iv"] + q["ask_iv"])
    err, inside, n = [], [], 0
    for s in slices:
        sl = q[np.isclose(q["T"].values, s["T"], rtol=0, atol=1e-12)]
        if not len(sl):
            continue
        iv = np.sqrt(np.interp(sl["k"].values, GRID_NP, s[w_key]) / s["T"])
        err.append((iv - sl["mid_iv"].values) * 100)
        inside.append((iv >= sl["bid_iv"].values) & (iv <= sl["ask_iv"].values))
        n += len(sl)
    e = np.concatenate(err)
    return {"rmse_vol_pts": float(np.sqrt((e ** 2).mean())),
            "inside_bid_ask": float(np.concatenate(inside).mean()), "quotes": n}


def calendar_forensics(df: pd.DataFrame) -> dict:
    slices = _refit_slices(df)
    findings, prev = [], None
    # Replay the shipped rule: zero tolerance, compared on the overlap of the
    # two slices' *validated* ranges, which run to three times the quoted span.
    for s in slices:
        if not s["butterfly_ok"]:
            s["w_used"] = s["w_backbone"]
            s["status"] = "butterfly_fail"
        else:
            s["w_used"] = s["w_refined"]
            s["status"] = "ok"
        if prev is not None and s["status"] == "ok":
            lo = max(s["validated"][0], prev["validated"][0])
            hi = min(s["validated"][1], prev["validated"][1])
            m = (GRID_NP >= lo) & (GRID_NP <= hi)
            if m.any():
                d = s["w_used"][m] - prev["w_used"][m]
                i = int(np.argmin(d))
                k_at = GRID_NP[m][i]
                # The same comparison, restricted to where BOTH slices have
                # quotes rather than to the extrapolated validated range.
                qlo = max(s["k_lo"], prev["k_lo"])
                qhi = min(s["k_hi"], prev["k_hi"])
                mq = (GRID_NP >= qlo) & (GRID_NP <= qhi)
                dq = (s["w_used"][mq] - prev["w_used"][mq]) if mq.any() else np.array([np.nan])
                tol = min(s["spread_w"], prev["spread_w"])
                findings.append({
                    "dte": round(s["dte"], 2), "prev_dte": round(prev["dte"], 2),
                    "min_dw": float(d.min()), "k_at_min": float(k_at),
                    "quoted_k": [round(s["k_lo"], 3), round(s["k_hi"], 3)],
                    "checked_k": [round(lo, 3), round(hi, 3)],
                    "inside_quotes": bool(qlo <= k_at <= qhi),
                    "min_dw_within_quotes": float(np.nanmin(dq)),
                    "spread_in_w": tol,
                    "fails_as_shipped": bool(float(d.min()) < -1e-12),
                    "fails_if_quotes_only": bool(float(np.nanmin(dq)) < -1e-12),
                    "fails_if_spread_tolerant": bool(float(d.min()) < -tol),
                })
                if float(d.min()) < -1e-12:
                    s["status"] = "calendar_fail"
                    s["w_used"] = s["w_backbone"]
                if float(np.nanmin(dq)) < -1e-12:
                    s["status_quotes_only"] = "calendar_fail"
        prev = s

    # The counterfactual: the same acceptance rule, decided only where both
    # slices actually have quotes. Butterfly failures still demote.
    for s in slices:
        s["w_quotes_only"] = (s["w_backbone"] if not s["butterfly_ok"]
                              or s.get("status_quotes_only") == "calendar_fail"
                              else s["w_refined"])

    return {
        "slices": [{"dte": round(s["dte"], 2), "n": s["n"], "status": s["status"],
                    "quoted_k": [round(s["k_lo"], 3), round(s["k_hi"], 3)],
                    "checked_k": [round(s["validated"][0], 3), round(s["validated"][1], 3)]}
                   for s in slices],
        "pairs": findings,
        "shipped": _rmse_under(df, slices, "w_used"),
        "quotes_only_rule": _rmse_under(df, slices, "w_quotes_only"),
        "refined_everywhere": _rmse_under(df, slices, "w_refined"),
        "backbone_everywhere": _rmse_under(df, slices, "w_backbone"),
    }


def across_time(paths: list[str]) -> dict:
    rows = []
    for p in paths:
        df = pd.read_pickle(p)
        r = fit_chain(df, currency="BTC")
        rows.append({"file": p.split("/")[-1], "quotes": r.n_fit,
                     "rmse": round(r.refined["rmse_vol_pts"], 4),
                     "inside": round(r.refined["inside_bid_ask_share"], 4),
                     "fallbacks": len(r.refined["slices_fallback"]),
                     "which": sorted(r.refined["slices_fallback"])})
    rmses = [r["rmse"] for r in rows]
    fbs = [r["fallbacks"] for r in rows]
    return {"n": len(rows), "rmse_min": min(rmses), "rmse_max": max(rmses),
            "rmse_mean": round(float(np.mean(rmses)), 4),
            "fallbacks_min": min(fbs), "fallbacks_max": max(fbs), "runs": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chains", default="/tmp/chains/btc_*.pkl")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="research/fit_stability_results.json")
    a = ap.parse_args()

    paths = sorted(glob.glob(a.chains))
    if not paths:
        print("no cached chains; fetching one")
        df = fetch_chain("BTC")
    else:
        df = pd.read_pickle(paths[0])

    result = {"quotes": int(len(df))}
    print("1. determinism")
    result["determinism"] = determinism(df, a.repeats)
    print(json.dumps(result["determinism"], indent=1))

    print("\n2/3. calendar forensics")
    result["calendar"] = calendar_forensics(df)
    print(json.dumps(result["calendar"], indent=1))

    if len(paths) > 1:
        print("\n4. across chains over time")
        result["across_time"] = across_time(paths)
        print(json.dumps(result["across_time"], indent=1))

    with open(a.out, "w") as fh:
        json.dump(result, fh, indent=1, default=float)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
