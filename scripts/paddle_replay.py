"""Replay real Paddle events through the handler, into a scratch store.

    PYTHONPATH=src python scripts/paddle_replay.py
    PYTHONPATH=src python scripts/paddle_replay.py --save events.jsonl

Our tests build their own webhook payloads, which proves the state machine and
proves nothing about the wire: a field in the wrong place, a status spelled
differently, an id that turns out to be the adjustment's rather than the
subscription's. This pulls what Paddle actually delivered and runs it through
the same handler, then prints the subscriber state it produced.

Signature verification is off here on purpose. ``GET /events`` returns the
payload but not the ``Paddle-Signature`` header that came with the delivery, so
there is nothing to verify against; verification is covered separately by tests
that sign their own bodies. What this checks is the half those tests cannot:
that the shapes are what we assumed.

Nothing is written outside a temporary directory, and no live subscriber state
is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tmo.payments.client import PaddleAPIError, PaddleClient  # noqa: E402
from tmo.payments.config import PaddleConfig, PaddleConfigError  # noqa: E402
from tmo.payments.event_ledger import ProcessedEventLedger  # noqa: E402
from tmo.payments.handler import PaddleWebhookHandler  # noqa: E402
from tmo.payments.store import SubscriberStore  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paddle_setup import load_dotenv  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--save", help="also write the raw events to this file")
    ap.add_argument("--from-file", help="replay a saved file instead of calling Paddle")
    a = ap.parse_args(argv)

    if a.from_file:
        with open(a.from_file, encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        print(f"replaying {len(events)} events from {a.from_file}")
        tier_map = os.getenv("TMO_TIER_PRICE_MAP", "")
    else:
        load_dotenv(a.env_file)
        try:
            cfg = PaddleConfig.from_env()
        except PaddleConfigError as exc:
            print(f"config: {exc}")
            return 2
        if not cfg.api_key:
            print("no TMO_PADDLE_API_KEY set")
            return 2
        try:
            events = list(PaddleClient(cfg).iter_events())[:a.limit]
        except PaddleAPIError as exc:
            print(f"could not read events: {exc}")
            return 1
        tier_map = cfg.tier_price_map
        print(f"pulled {len(events)} delivered events from the {cfg.mode} account")

    if a.save:
        with open(a.save, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
        print(f"saved to {a.save}")

    kinds = Counter(e.get("event_type", "?") for e in events)
    print("\nwhat Paddle sent:")
    for k, n in kinds.most_common():
        print(f"  {n:4d}  {k}")

    with tempfile.TemporaryDirectory() as tmp:
        handler = PaddleWebhookHandler(
            secret="",                                   # see the module docstring
            store=SubscriberStore(os.path.join(tmp, "subscribers.json"), cache_ttl_s=0),
            event_ledger=ProcessedEventLedger(os.path.join(tmp, "events.json")),
            tier_price_map=tier_map,
        )
        outcomes = Counter()
        for e in sorted(events, key=lambda x: x.get("occurred_at", "")):
            result = handler.handle(body=json.dumps(e).encode(), signature_header="")
            key = f"{e.get('event_type', '?')} -> " + (
                "handled" if result.get("handled") else result.get("reason", "?"))
            outcomes[key] += 1
        print("\nwhat the handler did:")
        for k, n in sorted(outcomes.items()):
            print(f"  {n:4d}  {k}")

        subs = handler.store.all()
        print(f"\nsubscribers after replay: {len(subs)}")
        for sid, e in subs.items():
            print(f"  {sid}  tier={e.get('tier')}  active={e.get('active')}  "
                  f"key={'yes' if e.get('api_key') else 'no'}  "
                  f"live={handler.store.is_active(sid)}")
            for adj in e.get("adjustments", []):
                print(f"      {adj['action']} {adj['type']} {adj['status']} "
                      f"-> revoked={adj['revoked']}")

    unhandled = {k: n for k, n in outcomes.items() if "-> handled" not in k}
    if unhandled:
        print("\nnot applied (each of these should have a reason you agree with):")
        for k, n in sorted(unhandled.items()):
            print(f"  {n:4d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
