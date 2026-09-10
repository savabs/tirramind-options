/**
 * Subscriber state in D1, and the one function that changes it.
 *
 * Everything a webhook does lands here, so the shape of the record and the
 * places it is read from stay in one file.
 */

import { decideAdjustment, effectiveActive, resolveTier } from "./rules";

export interface Subscriber {
  subscription_id: string;
  customer_id: string | null;
  email: string | null;
  tier: string | null;
  active: number;
  active_until: number | null;
  expires_at: number | null;
  api_key: string | null;
  updated_at: number;
}

const ACTIVE = new Set(["subscription.created", "subscription.updated",
  "subscription.activated", "subscription.revived", "subscription.trialing"]);
const GRACE = new Set(["subscription.canceled", "subscription.paused"]);
const PAST_DUE = new Set(["subscription.past_due"]);
const HARD_REVOKE = new Set(["subscription.expired"]);
const ADJUSTMENT = new Set(["adjustment.created", "adjustment.updated"]);

export const PAST_DUE_GRACE_S = 3 * 24 * 3600;

export function newApiKey(): string {
  const b = crypto.getRandomValues(new Uint8Array(24));
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return "tmo_" + btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || !value) return null;
  const ms = Date.parse(value);
  // Never fall back to zero: that reads as a date far in the past and would
  // revoke a paying customer.
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/** The paid-through date, present only on an event that grants access. */
export function paidThrough(data: Record<string, any>): number | null {
  const period = data.current_billing_period || {};
  return parseTimestamp(period.ends_at)
      ?? parseTimestamp((data.scheduled_change || {}).effective_at);
}

export async function get(db: D1Database, id: string): Promise<Subscriber | null> {
  return db.prepare("SELECT * FROM subscribers WHERE subscription_id = ?")
    .bind(id).first<Subscriber>();
}

export async function byEmail(db: D1Database, email: string): Promise<Subscriber | null> {
  if (!email) return null;
  return db.prepare(
    "SELECT * FROM subscribers WHERE email = ? ORDER BY updated_at DESC LIMIT 1")
    .bind(email.trim().toLowerCase()).first<Subscriber>();
}

export async function byIdentity(db: D1Database, sub: string): Promise<Subscriber | null> {
  if (!sub) return null;
  return db.prepare(
    `SELECT s.* FROM subscribers s JOIN identities i
       ON i.subscription_id = s.subscription_id WHERE i.sub = ?`)
    .bind(sub).first<Subscriber>();
}

export async function byApiKey(db: D1Database, key: string): Promise<Subscriber | null> {
  if (!key) return null;
  return db.prepare("SELECT * FROM subscribers WHERE api_key = ?").bind(key).first<Subscriber>();
}

export async function linkIdentity(db: D1Database, id: string, sub: string): Promise<void> {
  await db.prepare(
    "INSERT OR REPLACE INTO identities (sub, subscription_id, linked_at) VALUES (?, ?, ?)")
    .bind(sub, id, Date.now() / 1000).run();
}

async function upsert(db: D1Database, id: string, patch: Partial<Subscriber>): Promise<Subscriber> {
  const prior = await get(db, id);
  const next: Subscriber = {
    subscription_id: id,
    customer_id: patch.customer_id ?? prior?.customer_id ?? null,
    email: patch.email ?? prior?.email ?? null,
    tier: patch.tier ?? prior?.tier ?? null,
    active: patch.active ?? prior?.active ?? 0,
    // Only overwrite when the caller supplies something: most events have
    // nothing new to say about the paid-through date, and a bare cancellation
    // carries no billing period at all.
    active_until: patch.active_until ?? prior?.active_until ?? null,
    expires_at: patch.expires_at ?? prior?.expires_at ?? null,
    api_key: prior?.api_key ?? patch.api_key ?? null,
    updated_at: Date.now() / 1000,
  };
  await db.prepare(
    `INSERT INTO subscribers
       (subscription_id, customer_id, email, tier, active, active_until, expires_at, api_key, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(subscription_id) DO UPDATE SET
       customer_id=excluded.customer_id, email=excluded.email, tier=excluded.tier,
       active=excluded.active, active_until=excluded.active_until,
       expires_at=excluded.expires_at, api_key=excluded.api_key,
       updated_at=excluded.updated_at`)
    .bind(next.subscription_id, next.customer_id, next.email, next.tier, next.active,
          next.active_until, next.expires_at, next.api_key, next.updated_at).run();
  return next;
}

export interface Applied {
  handled: boolean;
  reason: string;
  subscription_id?: string;
  tier?: string | null;
  active?: boolean;
  revoked?: boolean;
}

/**
 * Apply one verified webhook to subscriber state.
 *
 * On an adjustment the subscription lives in its own field: `data.id` is the
 * adjustment's id, and reading it as the subscription would invent a subscriber
 * on every refund.
 */
export type EmailResolver = (customerId: string) => Promise<string | null>;

export async function apply(db: D1Database, event: Record<string, any>,
                            tierMap: string, now = Date.now() / 1000,
                            resolveEmail?: EmailResolver): Promise<Applied> {
  const type = String(event.event_type || "");
  const data = event.data || {};
  const id = ADJUSTMENT.has(type)
    ? data.subscription_id
    : (data.id || data.subscription_id || (data.subscription || {}).id);
  if (!id) return { handled: false, reason: "no subscription_id" };

  if (ADJUSTMENT.has(type)) {
    const existing = await get(db, id);
    if (!existing) return { handled: false, reason: "unknown subscription", subscription_id: id };
    const d = decideAdjustment(data.action, data.status, data.type);
    if (!d.applied) return { handled: false, reason: d.reason, subscription_id: id };
    if (d.revoke) await upsert(db, id, { active: 0, active_until: now, expires_at: now });
    await db.prepare(
      `INSERT OR REPLACE INTO adjustments
         (adjustment_id, subscription_id, action, status, kind, revoked, recorded_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(String(data.id || crypto.randomUUID()), id, data.action, data.status,
            data.type, d.revoke ? 1 : 0, now).run();
    return { handled: true, reason: d.reason, subscription_id: id, revoked: d.revoke,
             active: d.revoke ? false : undefined };
  }

  const customerId = data.customer_id || (data.customer || {}).id || null;
  const email = ((data.customer || {}).email || null);
  const priceId = ((data.items || [])[0] || {}).price?.id ?? null;
  const tier = resolveTier(priceId, tierMap);

  if (ACTIVE.has(type)) {
    // Fail closed: we provision only prices we sell.
    if (!tier) return { handled: false, reason: "price is not mapped to a product tier",
                        subscription_id: id };
    const prior = await get(db, id);
    // Look the email up only when we do not already have one: it is a network
    // call on the hot path of a webhook, and it never changes for a customer.
    let resolved = email ? String(email).toLowerCase() : null;
    if (!resolved && !prior?.email && customerId && resolveEmail) {
      resolved = await resolveEmail(String(customerId));
    }
    const record = await upsert(db, id, {
      customer_id: customerId, email: resolved ?? undefined,
      tier, active: 1, active_until: paidThrough(data) ?? undefined,
      api_key: prior?.api_key ?? newApiKey(),
    });
    return { handled: true, reason: "granted", subscription_id: id, tier: record.tier, active: true };
  }
  if (GRACE.has(type)) {
    // Passing the extracted value, usually null, leaves the paid-through date
    // captured earlier alone, which keeps the key alive to the end of the period.
    await upsert(db, id, { customer_id: customerId, active: 0,
                           active_until: paidThrough(data) ?? undefined });
    return { handled: true, reason: "cancelled or paused; access runs to the paid-through date",
             subscription_id: id, active: false };
  }
  if (PAST_DUE.has(type)) {
    const prior = await get(db, id);
    const grace = now + PAST_DUE_GRACE_S;
    // Never shrink a longer window already on file.
    const until = prior?.active_until != null ? Math.max(prior.active_until, grace) : grace;
    await upsert(db, id, { customer_id: customerId, active: 0, active_until: until });
    return { handled: true, reason: "past due; a grace window, not a revocation",
             subscription_id: id, active: false };
  }
  if (HARD_REVOKE.has(type)) {
    await upsert(db, id, { customer_id: customerId, active: 0, active_until: now, expires_at: now });
    return { handled: true, reason: "expired; revoked now", subscription_id: id, active: false };
  }
  return { handled: false, reason: "unhandled event", subscription_id: id };
}

export function entitlement(record: Subscriber | null, now = Date.now() / 1000) {
  if (!record) {
    return { entitled: false, tier: null, reason: "no subscription is linked to this account",
             subscription_id: null, active_until: null, needs_link: true };
  }
  const live = effectiveActive(
    { active: Boolean(record.active), active_until: record.active_until,
      expires_at: record.expires_at }, now);
  return { entitled: live, tier: record.tier,
           reason: live ? "active subscription" : "the subscription is not active",
           subscription_id: record.subscription_id, active_until: record.active_until,
           needs_link: false };
}
