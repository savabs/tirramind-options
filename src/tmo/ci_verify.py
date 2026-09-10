"""The gate. Row counts are evidence; exit codes are not.

Fails the run when today produced no mark for a strategy that should have one,
or when the surface we published was not arbitrage-clean. Everything else is
printed and tolerated: a quiet day is data, a missing day is a hole.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import ledger, specs


def check(*, root: str = ledger.DEFAULT_ROOT, spec_dir: str = specs.SPEC_DIR,
          as_of: str | None = None) -> tuple[bool, list[str]]:
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    live = specs.active(specs.load_all(spec_dir))
    marks = [m for m in ledger.read("marks", root=root) if m["as_of"] == as_of]
    fits = [f for f in ledger.read("fits", root=root) if f["as_of"] == as_of]
    problems, notes = [], []

    have = {m["strategy_id"] for m in marks}
    for spec in live:
        sid = spec["strategy_id"]
        if sid not in have:
            problems.append(f"no mark for active strategy {sid} on {as_of}")
    if not fits:
        problems.append(f"no fit recorded on {as_of}")
    for f in fits:
        if f.get("our_butterfly_violations") or f.get("our_calendar_violations"):
            problems.append(f"{f['fit_id']}: our own surface violates no-arbitrage")
        notes.append(f"{f['fit_id']}: rmse {f['refined_rmse_vol_pts']:.3f} vol pts, "
                     f"{f['refined_inside_bid_ask']:.1%} inside the spread, "
                     f"{f['executable_venue_arbs']} executable venue arbs")
    for m in marks:
        notes.append(f"{m['strategy_id']}: equity ${m['equity_usd']:,.2f}, "
                     f"{m['open_positions']} open, {m['n_trades_today']} fills, {m['note']}")
        if m.get("missing_instruments"):
            notes.append(f"  imputed marks for {m['missing_instruments']}")

    errs = [e for e in ledger.read("errors", root=root) if e["as_of"] == as_of]
    for e in errs:
        problems.append(f"error recorded for {e.get('strategy_id') or 'the run'}")
    return not problems, notes + [f"PROBLEM: {p}" for p in problems]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ledger.DEFAULT_ROOT)
    ap.add_argument("--specs", default=specs.SPEC_DIR)
    ap.add_argument("--as-of", default=None)
    a = ap.parse_args(argv)
    ok, lines = check(root=a.root, spec_dir=a.specs, as_of=a.as_of)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
