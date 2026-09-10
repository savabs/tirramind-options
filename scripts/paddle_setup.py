"""Bring a Paddle sandbox account to the state the Terminal needs, idempotently.

    PYTHONPATH=src python scripts/paddle_setup.py            # look, change nothing
    PYTHONPATH=src python scripts/paddle_setup.py --apply    # create what is missing

Reads credentials from the environment, or from a local ``.env`` that is not in
the repository. Prints identifiers and prices; never prints a key, a token or a
secret. Dry run by default, because creating a product in a billing account is
not something a script should do because it was run by accident.

What it cannot do, and will tell you to do yourself:

  * generate an API key -- that happens in the Paddle dashboard
  * create the notification destination and read back its secret -- the secret
    is shown once, in the dashboard, and belongs in your ``.env``
  * complete a checkout -- a card, even a sandbox one, is entered by a person
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tmo.payments.client import PaddleAPIError, PaddleClient  # noqa: E402
from tmo.payments.config import PaddleConfig, PaddleConfigError  # noqa: E402

PRODUCT_NAME = "Terminal"
PRODUCT_DESCRIPTION = (
    "A bring-your-own-broker options terminal: arbitrage-checked volatility "
    "surfaces published with their error, positions with greek attribution that "
    "reconciles, and an executable-arbitrage monitor. Software only."
)
PRICES = [
    {"currency_code": "INR", "amount_minor": 49900, "description": "Terminal monthly (INR)"},
    {"currency_code": "USD", "amount_minor": 1900, "description": "Terminal monthly (USD)"},
]


def load_dotenv(path: str = ".env") -> int:
    """Populate the environment from a local file. Values are never printed."""
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
                n += 1
    return n


def money(p: dict) -> str:
    up = p.get("unit_price", {})
    amount = int(up.get("amount", 0)) / 100
    cycle = p.get("billing_cycle") or {}
    every = f"/{cycle.get('interval', 'one-off')}" if cycle else ""
    return f"{up.get('currency_code', '?')} {amount:,.2f}{every}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="create what is missing")
    ap.add_argument("--env-file", default=".env")
    a = ap.parse_args(argv)

    loaded = load_dotenv(a.env_file)
    print(f"loaded {loaded} settings from {a.env_file}" if loaded
          else f"no {a.env_file}; using the environment as it is")

    try:
        cfg = PaddleConfig.from_env()
    except PaddleConfigError as exc:
        print(f"config: {exc}")
        return 2
    if not cfg.api_key:
        print("no TMO_PADDLE_API_KEY set. Generate one in the Paddle dashboard\n"
              "  (Developer tools -> Authentication -> API keys) and put it in .env.\n"
              "  Nothing else here can run without it.")
        return 2

    client = PaddleClient(cfg)
    try:
        who = client.whoami()
    except PaddleAPIError as exc:
        print(f"the key did not work: {exc}")
        if exc.status in (401, 403):
            print("  401/403 means withdrawn, revoked, or from the other mode "
                  "(a sandbox key cannot talk to live).")
        return 1
    print(f"reachable: {cfg.mode} account, {who['events_visible']} event(s) visible")

    products = client.list_products()
    terminal = next((p for p in products if p.get("name") == PRODUCT_NAME
                     and p.get("status") == "active"), None)
    print(f"\nproducts: {len(products)}")
    for p in products:
        mark = "->" if p is terminal else "  "
        print(f" {mark} {p['id']}  {p.get('status', '?'):8}  {p.get('name')}")

    if terminal is None:
        if not a.apply:
            print(f"\nWOULD CREATE product {PRODUCT_NAME!r} (tax category saas). "
                  f"Re-run with --apply.")
            return 0
        terminal = client.create_product(name=PRODUCT_NAME, description=PRODUCT_DESCRIPTION)
        print(f"\ncreated product {terminal['id']}")

    existing = [p for p in client.list_prices()
                if p.get("product_id") == terminal["id"] and p.get("status") == "active"]
    have = {(p.get("unit_price", {}) or {}).get("currency_code") for p in existing}
    print(f"\nprices on {terminal['id']}: {len(existing)}")
    for p in existing:
        print(f"    {p['id']}  {money(p):>22}  {p.get('description', '')}")

    created = []
    for want in PRICES:
        if want["currency_code"] in have:
            continue
        if not a.apply:
            print(f"    WOULD CREATE {want['currency_code']} "
                  f"{want['amount_minor'] / 100:,.2f}/month")
            continue
        created.append(client.create_price(product_id=terminal["id"], **want))
        print(f"    created {created[-1]['id']}  {money(created[-1])}")

    all_prices = existing + created
    if all_prices:
        mapping = ",".join(f"{p['id']}:terminal" for p in all_prices)
        print("\nPut this in .env so these prices, and only these, provision access:")
        print(f"  TMO_TIER_PRICE_MAP={mapping}")

    dests = client.list_notification_settings()
    print(f"\nnotification destinations: {len(dests)}")
    for d in dests:
        print(f"    {d.get('id')}  {'active' if d.get('active') else 'inactive':8}  "
              f"{d.get('destination', '?')}")
    if not dests:
        print("    none. Create one in the dashboard pointing at your /webhook,\n"
              "    subscribe it to subscription.* and adjustment.*, and copy the\n"
              "    endpoint secret into TMO_PADDLE_WEBHOOK_SECRET. The secret is\n"
              "    shown once and cannot be read back through the API.")

    if not a.apply:
        print("\n(dry run: nothing was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
