# The ledger

A public, pre-registered, append-only track record. It started on 2026-09-10
and runs every day whether or not anyone is watching.

## Why it exists

The product this repo sells is an options terminal. The thing that outlives the
product is an accountable record of systematic strategies, published before the
fact and never edited after it. That record is the asset. It is also the only
honest answer to "why should I believe your numbers", which is the question
every competitor in this category avoids.

## The rules, and why they cannot move

Each strategy is a JSON file in `specs/`. It states the universe, entry, exit,
sizing, hedging, costs and marking before a single position is opened. The file
is hashed canonically and the hash goes into `data/ledgers/registry.jsonl`.

Editing a registered file and re-running does not overwrite that row. It fails
the run, unless the file bumps its `version` and names the hash it
`supersedes`, in which case both rows stand and the change is visible forever.
Retiring a rule is allowed. Rewriting history is not.

## What is recorded

| Ledger | One row per | Key |
|---|---|---|
| `registry.jsonl` | strategy version ever registered | `spec_hash` |
| `fits.jsonl` | daily surface fit, with its full error report | `fit_id` |
| `trades.jsonl` | paper fill | `trade_id` |
| `marks.jsonl` | strategy, per day | `mark_id` |
| `errors.jsonl` | failure, with traceback | `error_id` |

The raw Deribit chain behind every decision is archived content-addressed under
`data/snapshots/`, so any day's fit can be re-run against the exact bytes it
was made on.

## How the fills are priced

Every paper fill crosses the spread and pays the venue's published taker fee.
We sell at the bid and buy at the ask, never at the mid. Options pay the lower
of 0.03% of the forward and 12.5% of the premium; forwards pay 0.05% of
notional. Each book starts with zero cash, so its equity is simply cumulative
profit and loss in USD, and it starts negative by the cost of getting in.

## What is being tracked today

- **btc-weekly-vrp** sells the front-week at-the-money BTC straddle and
  delta-hedges it daily off our arbitrage-checked surface. It is the reference
  case: the plainest systematic option strategy there is. Anything cleverer
  that we register later has to beat it.
- **btc-hold-context** holds one BTC and never trades again. It is context, not
  a benchmark. A delta-hedged volatility strategy is not trying to beat long
  BTC, and presenting them as competitors would be dishonest.

Negative results stay published.

## A change in the engine, on the record

The daily fit is produced by [voltorch](https://pypi.org/project/voltorch/), and
its version is recorded on every row from 2026-09-11 onwards. It matters: the
published error is a property of the engine as much as of the market.

**2026-09-10, voltorch 0.2.0 to 0.2.1.** The engine rejected a refined expiry
whenever its total variance dipped below the previous expiry's anywhere out to
three times the quoted range, which reaches a strike at 13% of the forward.
Measured over eight chains, that rejected 23 slices and none of them failed the
same test where both expiries actually have quotes. 0.2.1 checks where the quotes
are, and claims no more than it checks. The published error fell from about 0.75
to about 0.70 volatility points and its run-to-run spread halved.

Rows written before that change carry the old rule. The fit rows themselves hold
the full report, including the guarantee text, so which rule produced a given row
is readable from the row. The working is in `research/fit_stability.md`.

## Running it

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m tmo.daily      # writes rows
PYTHONPATH=src python -m tmo.ci_verify  # the gate; exit code means something here
```
