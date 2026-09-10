"""The daily job. Fetch, fit, register, step every active strategy, mark.

Exit code is not evidence. This writes rows and prints counts; ``ci_verify``
is the gate that decides whether the day was real. A source that goes quiet
must show up as a missing row, not as a green tick.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

from . import ledger, market, portfolio, specs, store
from .strategies import REGISTRY


def run(*, root: str = ledger.DEFAULT_ROOT, snapshot_root: str = store.DEFAULT_ROOT,
        spec_dir: str = specs.SPEC_DIR, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    as_of = now.strftime("%Y-%m-%d")
    ts = now.isoformat(timespec="seconds")

    all_specs = specs.load_all(spec_dir)
    n_reg = specs.register(all_specs, root=root, now=now)
    live = specs.active(all_specs)

    done = ledger.keys("marks", "mark_id", root=root)
    live_ids = {s["strategy_id"] for s in live}
    fitted = f"BTC|{as_of}" in ledger.keys("fits", "fit_id", root=root)
    if fitted and all(f"{sid}|{as_of}" in done for sid in live_ids):
        # Nothing left to decide today. Return before touching the network so a
        # second run neither burns a fit nor writes a near-identical snapshot.
        return {"as_of": as_of, "specs_registered": n_reg,
                "strategies": {sid: "already marked today" for sid in sorted(live_ids)}}

    marks, report, meta = market.state("BTC", snapshot_root=snapshot_root)
    ledger.append("fits", [{
        "fit_id": f"BTC|{as_of}", "as_of": as_of, "captured_at": ts, **meta,
        "report": json.loads(report.to_json()),
    }], "fit_id", root=root)

    summary = {"as_of": as_of, "specs_registered": n_reg, "strategies": {}, **meta}

    for spec in live:
        sid = spec["strategy_id"]
        mark_id = f"{sid}|{as_of}"
        if mark_id in done:
            summary["strategies"][sid] = "already marked today"
            continue
        fn = REGISTRY.get(sid)
        if fn is None:
            summary["strategies"][sid] = "no implementation registered"
            continue
        h = specs.spec_hash(spec)
        history = [t for t in ledger.read("trades", root=root) if t["strategy_id"] == sid]
        book = portfolio.replay(history)
        try:
            new_trades, note = fn(spec, book, marks)
        except Exception:
            summary["strategies"][sid] = "FAILED"
            ledger.append("errors", [{
                "error_id": f"{sid}|{as_of}", "as_of": as_of, "strategy_id": sid,
                "traceback": traceback.format_exc()[-2000:]}], "error_id", root=root)
            continue

        rows = []
        base = len([t for t in history if t["as_of"] == as_of])
        for i, t in enumerate(new_trades):
            seq = base + i
            rows.append({"trade_id": portfolio.trade_id(sid, as_of, seq, t),
                         "strategy_id": sid, "spec_hash": h, "as_of": as_of,
                         "captured_at": ts, "seq": seq, **t})
        # Trades first, then the mark. The mark row is the day's guard, so a
        # crash between the two re-runs the day rather than skipping it; the
        # trade ids are content-derived, so an identical replay adds nothing.
        ledger.append("trades", rows, "trade_id", root=root)
        book = portfolio.replay(history + rows)
        v = portfolio.value(book, marks, price_col="mid_usd")
        ledger.append("marks", [{
            "mark_id": mark_id, "as_of": as_of, "captured_at": ts, "strategy_id": sid,
            "spec_hash": h, "note": note, "n_trades_today": len(rows),
            "open_positions": len(book.positions), **v,
        }], "mark_id", root=root)
        summary["strategies"][sid] = f"{note}; {len(rows)} fills; equity ${v['equity_usd']:,.2f}"
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=ledger.DEFAULT_ROOT)
    ap.add_argument("--snapshots", default=store.DEFAULT_ROOT)
    ap.add_argument("--specs", default=specs.SPEC_DIR)
    a = ap.parse_args(argv)
    try:
        s = run(root=a.root, snapshot_root=a.snapshots, spec_dir=a.specs)
    except Exception:
        traceback.print_exc()
        os.makedirs(a.root, exist_ok=True)
        ledger.append("errors", [{
            "error_id": f"run|{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "strategy_id": None, "traceback": traceback.format_exc()[-2000:]}],
            "error_id", root=a.root)
        return 0  # the gate is ci_verify, not this exit code
    print(json.dumps(s, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
