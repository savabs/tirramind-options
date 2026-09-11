/**
 * Send a sample alert to one of your own webhooks.
 *
 * Every alerting product needs this, for the reason the delivery log exists: an
 * alert that silently stops arriving is worse than no alerts at all. This is how
 * somebody checks the wire is live without waiting for the market to misbehave.
 *
 * It is marked as a test in the payload, so a receiver can tell it apart from
 * money actually being on the table.
 */

import { readCookie, verify } from "../../../lib/token";
import { byEmail, byIdentity, entitlement } from "../../../lib/subscribers";
import { deliver, type Subscription } from "../../../lib/alerts";

interface Env { DB: D1Database; SESSION_SECRET: string }

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  if (!identity) return json(401, { error: "sign in first" });
  const record = (await byIdentity(ctx.env.DB, identity.sub))
              ?? (await byEmail(ctx.env.DB, identity.email));
  const ent = entitlement(record);
  if (!ent.entitled) return json(403, { error: "alerts are part of the paid tier" });

  const id = new URL(ctx.request.url).searchParams.get("id") || "";
  const sub = await ctx.env.DB.prepare(
    `SELECT id, subscription_id, kind, url, secret, min_edge_usd, currency
       FROM alert_subscriptions WHERE id = ? AND subscription_id = ?`)
    .bind(id, record!.subscription_id).first<Subscription>();
  if (!sub) return json(404, { error: "no alert of yours with that id" });

  const out = await deliver(sub, {
    kind: "test", as_of: new Date().toISOString(), currency: sub.currency || "BTC",
    items: [{ test: true, note: "A sample delivery. Nothing is wrong with the book." }],
    terminal: "https://tirramind-options.pages.dev/terminal",
  });
  const now = Date.now() / 1000;
  await ctx.env.DB.batch([
    ctx.env.DB.prepare(
      `INSERT INTO alert_deliveries (id, subscription, sent_at, status, ok, detail, items)
       VALUES (?, ?, ?, ?, ?, ?, 0)`)
      .bind(crypto.randomUUID(), sub.id, now, out.status, out.ok ? 1 : 0, `test: ${out.detail}`),
    ctx.env.DB.prepare("UPDATE alert_subscriptions SET last_sent_at = ?, last_error = ? WHERE id = ?")
      .bind(now, out.ok ? null : out.detail, sub.id),
  ]);
  return json(out.ok ? 200 : 502, out);
};
