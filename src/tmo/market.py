"""One day's market state: Deribit chain, voltorch fit, per-instrument marks.

Everything here is free and public -- Deribit's REST API needs no key -- which
is why the ledger can start before any broker relationship exists.

Two conventions matter and both are inherited from voltorch, verified there to
1e-4: an inverse contract's coin price times the forward is the undiscounted
USD price, and every fit is done on the forward with r = 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import voltorch
from voltorch import BlackScholes, fit_chain
from . import store, venues

BS = BlackScholes()


def greeks(F, K, T, sigma, is_call) -> dict[str, np.ndarray]:
    """Black-76 greeks by autograd, per voltorch's own pattern.

    Delta is with respect to the *forward*, because the hedge instrument on
    Deribit is a future, not spot. Theta is per year; divide by 365 for a day.
    """
    F = torch.tensor(np.asarray(F, dtype=float), requires_grad=True)
    K = torch.tensor(np.asarray(K, dtype=float))
    T = torch.tensor(np.asarray(T, dtype=float), requires_grad=True)
    s = torch.tensor(np.asarray(sigma, dtype=float), requires_grad=True)
    ic = torch.tensor(np.asarray(is_call, dtype=bool))
    price = BS(F, K, T, torch.zeros_like(K), s, is_call=ic)
    delta = torch.autograd.grad(price.sum(), F, create_graph=True)[0]
    gamma = torch.autograd.grad(delta.sum(), F, retain_graph=True)[0]
    vega = torch.autograd.grad(price.sum(), s, retain_graph=True)[0]
    theta = -torch.autograd.grad(price.sum(), T)[0]
    return {"price": price.detach().numpy(), "delta": delta.detach().numpy(),
            "gamma": gamma.detach().numpy(), "vega": vega.detach().numpy(),
            "theta": theta.detach().numpy()}


def _ref_iv_by_instrument(chain: pd.DataFrame, report) -> pd.Series:
    """Map the fit's per-slice refined IVs back onto every instrument.

    The fit only sees OTM two-sided quotes, so an ITM instrument has no fitted
    point of its own. It gets the surface value at its own log-moneyness, which
    is the same number its OTM twin carries: put-call parity means one surface
    serves both, and pricing an ITM option off its OTM twin's IV is exactly
    what the parity relation licenses.
    """
    out = pd.Series(np.nan, index=chain.index, dtype=float)
    for sl in report.slices:
        k_grid = np.asarray(sl["k"], dtype=float)
        iv_grid = np.asarray(sl["ref_iv"], dtype=float)
        order = np.argsort(k_grid)
        k_grid, iv_grid = k_grid[order], iv_grid[order]
        m = np.isclose(chain["T"].values, sl["T"], rtol=0, atol=1e-12)
        if not m.any():
            continue
        k = np.log(chain.loc[m, "strike"].values / chain.loc[m, "forward"].values)
        # np.interp clamps outside the quoted range rather than extrapolating.
        # Flat wings are wrong, but a flat wrong number beats an SVI wing
        # extrapolated past any quote, and the deviation filters exclude the
        # far wings anyway.
        out.loc[m] = np.interp(k, k_grid, iv_grid)
    return out


def state(market: str, *, snapshot_root: str = store.DEFAULT_ROOT,
          capture: bool = True) -> tuple[pd.DataFrame, object, dict]:
    """Fetch, archive and fit one market's chain.

    Returns ``(marks, report, meta)``. ``marks`` carries one row per live
    instrument with the venue's quotes, our refined IV, and greeks priced off
    our IV. The raw chain is snapshotted content-addressed before anything is
    fitted, so a fit that turns out to be wrong can be re-run against the exact
    bytes the decision was made on.
    """
    m = venues.BY_KEY[market]
    chain = venues.fetch(m)
    if chain.empty:
        raise RuntimeError(f"{market}: {m.venue} returned no live options")
    # The convention is checked on every fetch, not once when it was written.
    # Getting inverse and linear the wrong way round does not raise; it produces
    # a plausible-looking surface that is wrong by the price of the underlying.
    convention = venues.verify_convention(chain)
    # The engine version travels with the row. The published error is a
    # property of the engine as much as of the market, and a reader comparing
    # two rows months apart has no other way to know the rule changed under
    # them: 0.2.1 narrowed the calendar check and moved the error by a third.
    meta = {"currency": market, "market": market, "venue": m.venue, "base": m.base,
            "settled_in": m.settled_in, "convention": m.convention,
            "engine": "voltorch", "engine_version": voltorch.__version__,
            "convention_check_vol_pts": convention["median_vol_pts"],
            "n_quotes": int(len(chain))}
    if capture:
        path, digest, status = store.snapshot(f"{m.venue}_{market.lower()}", chain,
                                              root=snapshot_root)
        meta |= {"snapshot_digest": digest, "snapshot_status": status, "snapshot_path": path}

    report = fit_chain(chain, currency=market)
    marks = chain.copy()
    marks["ref_iv"] = _ref_iv_by_instrument(marks, report)
    marks["mid_usd"] = 0.5 * (marks["bid_usd"] + marks["ask_usd"])
    marks["dte"] = marks["T"] * 365.0
    ok = marks["ref_iv"].notna()
    g = greeks(marks.loc[ok, "forward"], marks.loc[ok, "strike"], marks.loc[ok, "T"],
               marks.loc[ok, "ref_iv"], marks.loc[ok, "is_call"])
    for name, vals in g.items():
        col = "our_usd" if name == "price" else name
        marks[col] = np.nan
        marks.loc[ok, col] = vals
    meta |= {"n_fit": report.n_fit, "expiries": report.expiries,
             "refined_rmse_vol_pts": report.refined["rmse_vol_pts"],
             "refined_inside_bid_ask": report.refined["inside_bid_ask_share"],
             "our_butterfly_violations": report.our_violations["butterfly_violations"],
             "our_calendar_violations": report.our_violations["calendar_violations"],
             # Sum the lists, do not count the dict. The executable report is
             # keyed by kind -- butterfly, vertical, calendar -- so len() of it
             # is always three, and read as a violation count it claims three
             # arbitrages on a clean book. It said exactly that on the public
             # page for a day.
             "executable_venue_arbs": sum(
                 len(v or []) for v in
                 (report.venue_violations.get("executable") or {}).values())}
    return marks, report, meta
