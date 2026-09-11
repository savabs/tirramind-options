/**
 * Manage your own alerts. Paid, and scoped to your own subscription.
 *
 * Every query is filtered by the caller's subscription id rather than by an id
 * they supply, so there is no shape of request that reaches somebody else's
 * webhook or its delivery log.
 */

import { readCookie, verify } from "../../lib/token";
import { byEmail, byIdentity, entitlement } from "../../lib/subscribers";
import { checkUrl } from "../../lib/alerts";

interface Env { DB: D1Database; SESSION_SECRET: string }

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

const KINDS = new Set(["executable_arb", "surface_integrity"]);

async function caller(ctx: EventContext<Env, string, unknown>) {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  if (!identity) return { error: json(401, { error: "sign in first" }) };
  const record = (await byIdentity(ctx.env.DB, identity.sub))
              ?? (await byEmail(ctx.env.DB, identity.email));
  const ent = entitlement(record);
  if (!ent.entitled) return { error: json(403, { error: "alerts are part of the paid tier", ...ent }) };
  return { owner: record!.subscription_id, sub: identity.sub };
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const who = await caller(ctx);
  if (who.error) return who.error;
  const { results } = await ctx.env.DB.prepare(
    `SELECT id, kind, url, min_edge_usd, currency, active, created_at, last_sent_at, last_error
       FROM alert_subscriptions WHERE subscription_id = ? ORDER BY created_at DESC`)
    .bind(who.owner).all();
  const { results: sent } = await ctx.env.DB.prepare(
    `SELECT d.id, d.subscription, d.sent_at, d.status, d.ok, d.detail, d.items
       FROM alert_deliveries d JOIN alert_subscriptions s ON s.id = d.subscription
      WHERE s.subscription_id = ? ORDER BY d.sent_at DESC LIMIT 20`)
    .bind(who.owner).all();
  // The log is shown, not just kept: an alert that silently stopped arriving is
  // worse than no alerts, and this is where that becomes visible.
  return json(200, { alerts: results, recent_deliveries: sent });
};

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const who = await caller(ctx);
  if (who.error) return who.error;

  let body: { kind?: string; url?: string; min_edge_usd?: number; currency?: string };
  try {
    body = await ctx.request.json();
  } catch {
    return json(400, { error: "body is not valid JSON" });
  }
  const kind = String(body.kind || "executable_arb");
  if (!KINDS.has(kind)) return json(400, { error: `kind must be one of ${[...KINDS].join(", ")}` });
  const checked = checkUrl(String(body.url || ""));
  if (!checked.ok) return json(400, { error: checked.why });

  const count = await ctx.env.DB.prepare(
    "SELECT COUNT(*) AS n FROM alert_subscriptions WHERE subscription_id = ?")
    .bind(who.owner).first<{ n: number }>();
  if ((count?.n ?? 0) >= 10) return json(400, { error: "ten alerts is enough" });

  const id = crypto.randomUUID();
  // The receiver verifies deliveries with this. It is shown once, here, and
  // never listed again: a secret that can be read back is a secret in a log.
  const secret = "tmoa_" + crypto.randomUUID().replace(/-/g, "");
  await ctx.env.DB.prepare(
    `INSERT INTO alert_subscriptions
       (id, subscription_id, sub, kind, url, secret, min_edge_usd, currency, active, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)`)
    .bind(id, who.owner, who.sub, kind, checked.url, secret,
          Number(body.min_edge_usd) || 0,
          body.currency ? String(body.currency).toUpperCase() : null,
          Date.now() / 1000).run();
  return json(200, { id, kind, url: checked.url, secret,
    note: "Keep this secret: it is how you verify a delivery came from us, and it is not shown again." });
};

export const onRequestDelete: PagesFunction<Env> = async (ctx) => {
  const who = await caller(ctx);
  if (who.error) return who.error;
  const id = new URL(ctx.request.url).searchParams.get("id") || "";
  // Scoped to the caller's own subscription, so an id from somewhere else
  // deletes nothing.
  const r = await ctx.env.DB.prepare(
    "DELETE FROM alert_subscriptions WHERE id = ? AND subscription_id = ?")
    .bind(id, who.owner).run();
  return json(200, { removed: r.meta.changes ?? 0 });
};
