/**
 * The rules that decide who may use a paid product.
 *
 * These moved to the edge when the webhook did, because billing that depends on
 * one machine being up is billing that stops when that machine stops. The cost
 * is that a second implementation now exists: `src/tmo/payments/store.py` has
 * the same rules in Python.
 *
 * Two implementations of a money rule is a real risk and it is answered rather
 * than hoped away. `rules.json` beside this file is a table of cases, and both
 * test suites read it. Neither can change a rule without the other failing.
 */

export interface Entry {
  active?: boolean | number;
  active_until?: number | null;
  expires_at?: number | null;
}

/**
 * Is this subscriber's access valid right now.
 *
 * Precedence, most restrictive first:
 *   1. `expires_at` is a hard ceiling. Past it, nothing else matters. This is
 *      how a refund revokes at once, over a period already paid for.
 *   2. `active_until` in the future grants access even when `active` is false,
 *      which is what makes a cancellation honour the period the customer bought
 *      and gives a failed card a grace window instead of a lockout.
 *   3. Otherwise the plain flag, so a record written before either timestamp
 *      existed behaves exactly as it did.
 */
export function effectiveActive(entry: Entry | null | undefined, now: number): boolean {
  if (!entry) return false;
  const expires = entry.expires_at;
  if (expires !== null && expires !== undefined && now >= expires) return false;
  const until = entry.active_until;
  if (until !== null && until !== undefined && now < until) return true;
  return Boolean(entry.active);
}

/**
 * Which tier a price sells, or null.
 *
 * Null means we do not sell it, and activation refuses. Deliberately
 * fail-closed: a billing system that grants access on an unrecognised price is
 * not a billing system. The cost is that adding a price without adding it here
 * leaves new customers unprovisioned, which is visible and fixable in a minute.
 */
export function parseTierMap(raw: string | null | undefined): Map<string, string> {
  const out = new Map<string, string>();
  for (const pair of (raw || "").split(",")) {
    const at = pair.indexOf(":");
    if (at < 0) continue;
    const id = pair.slice(0, at).trim();
    const tier = pair.slice(at + 1).trim();
    if (id && tier) out.set(id, tier);
  }
  return out;
}

export function resolveTier(priceId: string | null | undefined,
                            raw: string | null | undefined): string | null {
  if (!priceId) return null;
  return parseTierMap(raw).get(priceId) ?? null;
}

/** A refund the customer's money actually went back for. */
const SETTLED = new Set(["approved"]);
/** A chargeback is the bank reversing payment over our head; it revokes. */
const REVOKING = new Set(["refund", "chargeback"]);

export interface AdjustmentDecision {
  applied: boolean;
  revoke: boolean;
  reason: string;
}

export function decideAdjustment(action: string, status: string, kind: string): AdjustmentDecision {
  const a = (action || "").toLowerCase();
  const s = (status || "").toLowerCase();
  const k = (kind || "").toLowerCase();
  if (!REVOKING.has(a)) {
    return { applied: false, revoke: false, reason: `adjustment action ${a || "none"} does not affect access` };
  }
  if (a === "refund" && !SETTLED.has(s)) {
    return { applied: false, revoke: false, reason: `refund is ${s || "unstated"}, not settled` };
  }
  // A partial refund is a goodwill gesture on a live subscription. A chargeback
  // is not: the money is gone whatever its size.
  const revoke = a === "chargeback" || k === "full";
  return { applied: true, revoke,
           reason: revoke ? "full refund or chargeback" : "partial refund, access unchanged" };
}
