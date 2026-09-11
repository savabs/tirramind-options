/**
 * Work out who should be told about this fit, and tell them.
 *
 * Runs in the background after a fit is archived. Everything here is
 * best-effort by design: a dead receiver is that subscriber's problem to see in
 * their delivery log, not a reason for the archive to fail.
 */

import {
  arbPayload, deliver, fingerprint, integrityPayload, matches, QUIET_S,
  type Arb, type Subscription, type Surface,
} from "./alerts";

async function quiet(db: D1Database, subId: string, prints: string[], now: number) {
  if (!prints.length) return new Set<string>();
  const marks = prints.map(() => "?").join(",");
  const { results } = await db.prepare(
    `SELECT fingerprint FROM alert_seen
      WHERE subscription = ? AND last_sent_at > ? AND fingerprint IN (${marks})`)
    .bind(subId, now - QUIET_S, ...prints).all<{ fingerprint: string }>();
  return new Set(results.map((r) => r.fingerprint));
}

async function record(db: D1Database, subId: string, out: { ok: boolean; status: number | null;
                                                           detail: string }, items: number,
                      now: number) {
  await db.batch([
    db.prepare(
      `INSERT INTO alert_deliveries (id, subscription, sent_at, status, ok, detail, items)
       VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(crypto.randomUUID(), subId, now, out.status, out.ok ? 1 : 0, out.detail, items),
    db.prepare(
      `UPDATE alert_subscriptions SET last_sent_at = ?, last_error = ? WHERE id = ?`)
      .bind(now, out.ok ? null : out.detail, subId),
  ]);
}

export async function fanOut(db: D1Database, surface: Surface, now = Date.now() / 1000) {
  const { results } = await db.prepare(
    `SELECT id, subscription_id, kind, url, secret, min_edge_usd, currency
       FROM alert_subscriptions WHERE active = 1`).all<Subscription>();
  if (!results.length) return;

  const arbs: Arb[] = surface.arbs || [];
  const integrityBroken = surface.quality.our_butterfly_violations > 0
                       || surface.quality.our_calendar_violations > 0;

  for (const sub of results) {
    if (sub.kind === "surface_integrity") {
      if (!integrityBroken) continue;
      // One fingerprint per currency per day: an engine fault is worth saying
      // once, not ninety-six times.
      const print = `${surface.currency}:integrity:${surface.as_of.slice(0, 10)}`;
      if ((await quiet(db, sub.id, [print], now)).size) continue;
      const out = await deliver(sub, integrityPayload(surface));
      await record(db, sub.id, out, 1, now);
      if (out.ok) {
        await db.prepare(
          `INSERT OR REPLACE INTO alert_seen (subscription, fingerprint, last_sent_at)
           VALUES (?, ?, ?)`).bind(sub.id, print, now).run();
      }
      continue;
    }

    if (sub.kind !== "executable_arb") continue;
    const wanted = arbs.filter((a) => matches(sub, surface.currency, a));
    if (!wanted.length) continue;
    const prints = wanted.map((a) => fingerprint(surface.currency, a));
    const already = await quiet(db, sub.id, prints, now);
    const fresh = wanted.filter((_, i) => !already.has(prints[i]));
    if (!fresh.length) continue;

    const out = await deliver(sub, arbPayload(surface, fresh));
    await record(db, sub.id, out, fresh.length, now);
    // Only a delivered alert is marked seen. A failed one must be retried on the
    // next fit rather than quietly suppressed for six hours.
    if (out.ok) {
      await db.batch(fresh.map((a) => db.prepare(
        `INSERT OR REPLACE INTO alert_seen (subscription, fingerprint, last_sent_at)
         VALUES (?, ?, ?)`).bind(sub.id, fingerprint(surface.currency, a), now)));
    }
  }
}
