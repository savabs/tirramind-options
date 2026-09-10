"""Render a local sandbox checkout page, so a real subscription can be created.

    PYTHONPATH=src python scripts/paddle_checkout.py

Writes ``local/checkout.html``, which is outside the repository. With ``--serve``
it also serves that directory on a fixed port, because Paddle will not open a
checkout from a ``file://`` page: the account's default payment link names an
approved origin, and a local file has none. Open the printed URL in your own
browser and pay with Paddle's sandbox test card. That is the one step in the
billing path that a script must not do: entering card details, even fake ones,
is a person's job.

The client token this embeds is the public, client-side one. It is designed to
appear in a page and cannot move money on its own. It still does not belong in a
public repository, so the output goes to an ignored directory.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import ssl
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paddle_setup import load_dotenv  # noqa: E402
from tmo.payments.config import PaddleConfig  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terminal — sandbox checkout</title>
<style>
  body{{margin:0;background:#fcfcfb;color:#0b0b0b;
    font:16px/1.6 ui-sans-serif,-apple-system,system-ui,sans-serif}}
  .w{{max-width:560px;margin:0 auto;padding:56px 20px}}
  h1{{font-size:24px;margin:0 0 8px;letter-spacing:-.015em}}
  p{{color:#52514e;margin:0 0 16px}}
  .card{{border:1px solid #e3e2dd;border-radius:12px;background:#fff;padding:20px;margin:24px 0}}
  button{{appearance:none;border:0;border-radius:8px;background:#2a78d6;color:#fff;
    font:inherit;font-weight:600;padding:12px 20px;cursor:pointer;width:100%;margin-top:8px}}
  button:disabled{{background:#9ab; cursor:not-allowed}}
  code{{background:#f4f3f0;padding:2px 6px;border-radius:4px;font-size:14px}}
  .note{{font-size:14px;color:#84837c}}
  .sandbox{{display:inline-block;background:#eda100;color:#000;font-size:12px;font-weight:700;
    padding:2px 8px;border-radius:999px;letter-spacing:.04em}}
</style></head><body><div class="w">
<span class="sandbox">SANDBOX</span>
<h1>Terminal — test checkout</h1>
<p>This creates a real subscription in the Paddle sandbox, which is what makes real
subscription webhooks exist. No money moves.</p>

<div class="card">
  <p><b>Test card:</b> <code>4242 4242 4242 4242</code>, any future expiry,
  any 3-digit code, any postcode.</p>
  <p class="note">Use an email you can read; Paddle sends sandbox receipts to it.</p>
</div>

<div class="card">
  <button id="inr">Subscribe — ₹499 / month</button>
  <button id="usd">Subscribe — $19 / month</button>
  <p class="note" style="margin-top:14px">Prices: <code>{inr}</code> · <code>{usd}</code></p>
</div>

<p class="note">After paying, run
<code>PYTHONPATH=src python scripts/paddle_replay.py</code> to pull the events
Paddle recorded and run them through the handler.</p>

<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
  Paddle.Environment.set("sandbox");
  Paddle.Initialize({{ token: "{token}" }});
  const open = id => Paddle.Checkout.open({{ items: [{{ priceId: id, quantity: 1 }}] }});
  document.getElementById("inr").onclick = () => open("{inr}");
  document.getElementById("usd").onclick = () => open("{usd}");
</script>
</div></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help="serve local/ so the overlay can open")
    ap.add_argument("--port", type=int, default=8123)
    # Paddle stores the default payment link as https even when http was typed,
    # so the page has to be reachable over https for the origin to match. A
    # self-signed certificate is fine for a sandbox on your own machine; the
    # browser will ask you to accept it once.
    ap.add_argument("--https", action="store_true", help="serve over https with a local certificate")
    ap.add_argument("--cert", default=os.path.join("local", "dev-cert.pem"))
    ap.add_argument("--key", default=os.path.join("local", "dev-key.pem"))
    a = ap.parse_args(argv)
    load_dotenv()
    cfg = PaddleConfig.from_env()
    if not cfg.client_token:
        print("no TMO_PADDLE_CLIENT_TOKEN set; generate one in the Paddle dashboard")
        return 2
    prices = {}
    for pair in cfg.tier_price_map.split(","):
        pid, _, _tier = pair.partition(":")
        if pid.strip():
            prices[pid.strip()] = None
    ids = list(prices)
    if len(ids) < 2:
        print("expected two prices in TMO_TIER_PRICE_MAP; run scripts/paddle_setup.py --apply")
        return 2
    out = os.path.join("local", "checkout.html")
    os.makedirs("local", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(PAGE.format(token=cfg.client_token, inr=ids[0], usd=ids[1]))
    print(f"wrote {out}")
    if not a.serve:
        print("Run again with --serve, then open the printed URL in your own browser.")
        return 0

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory="local")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), handler) as httpd:
        scheme = "http"
        if a.https:
            if not (os.path.exists(a.cert) and os.path.exists(a.key)):
                print(f"missing {a.cert} or {a.key}. Generate them with:\n"
                      f'  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \\\n'
                      f'    -keyout {a.key} -out {a.cert} -subj "/CN=localhost" \\\n'
                      f'    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"')
                return 2
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(a.cert, a.key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"
        url = f"{scheme}://localhost:{a.port}/checkout.html"
        print(f"\nserving on {url}")
        print("This origin must match the account's default payment link in the\n"
              "Paddle dashboard, or the overlay will not open. Ctrl-C when done.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
