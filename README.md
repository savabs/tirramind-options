# TirraMind Options

A bring-your-own-broker options terminal, and a public track record that starts
before the product does.

- [`ledger/`](ledger/README.md) — the pre-registered, append-only track record.
  Live since 2026-09-10.
- `src/tmo/` — the daily job: fetch, fit, step every registered strategy, mark.
- `src/tmo/payments/` — Paddle billing for the Terminal subscription, and the
  webhook endpoint that provisions and revokes access.
- `scripts/paddle_*.py` — set up the sandbox, render a test checkout, and replay
  what Paddle actually delivered through the handler. Credentials come from a
  local `.env` that is not in this repository.
- `products/terminal/` — what the subscription is, and what it is not.
- `research/` — measurements, including the negative ones. Everything here is
  reproducible from the script beside it.
- `src/tmo/service/` — the fit service: an arbitrage-checked surface over HTTP,
  served warm. `deploy/` puts it on a box.
- `src/tmo/page.py` — the public Deribit tab: one self-contained HTML file that
  fetches nothing when you open it, refit every 30 minutes.

The surface engine is [voltorch](https://pypi.org/project/voltorch/):
arbitrage-checked eSSVI with per-slice SVI refinement. Every number here ships
with its error.

Software only. No recommendations, no advice, no signals.
