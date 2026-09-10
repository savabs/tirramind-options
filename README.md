# TirraMind Options

A bring-your-own-broker options terminal, and a public track record that starts
before the product does.

- [`ledger/`](ledger/README.md) — the pre-registered, append-only track record.
  Live since 2026-09-10.
- `src/tmo/` — the daily job: fetch, fit, step every registered strategy, mark.
- `src/tmo/payments/` — Paddle billing for the Terminal subscription, and the
  webhook endpoint that provisions and revokes access.
- `products/terminal/` — what the subscription is, and what it is not.

The surface engine is [voltorch](https://pypi.org/project/voltorch/):
arbitrage-checked eSSVI with per-slice SVI refinement. Every number here ships
with its error.

Software only. No recommendations, no advice, no signals.
