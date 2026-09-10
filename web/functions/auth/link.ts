/**
 * Bind this sign-in account to a subscription, using the key we emailed.
 *
 * For the person who paid with one address and signs in with another. The key is
 * the proof: only the subscriber ever received it.
 */

import { readCookie, verify } from "../../lib/token";
import { byApiKey, entitlement, linkIdentity } from "../../lib/subscribers";
import { effectiveActive } from "../../lib/rules";

interface Env { DB: D1Database; SESSION_SECRET: string }

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  if (!identity) return new Response(JSON.stringify({ error: "not signed in" }), { status: 401 });

  let key = "";
  try {
    key = String(((await ctx.request.json()) as any).api_key || "").trim();
  } catch { /* an empty key falls through to the same refusal */ }

  const record = key ? await byApiKey(ctx.env.DB, key) : null;
  const live = record && effectiveActive(
    { active: Boolean(record.active), active_until: record.active_until,
      expires_at: record.expires_at }, Date.now() / 1000);
  // Deliberately the same answer for an unknown key and an inactive one:
  // distinguishing them tells somebody guessing which keys exist.
  if (!record || !live) {
    return new Response(JSON.stringify(
      { error: "that key does not match an active subscription" }), { status: 403 });
  }

  await linkIdentity(ctx.env.DB, record.subscription_id, identity.sub);
  return new Response(JSON.stringify({ linked: true, email: identity.email,
    ...entitlement(record) }), { headers: { "Content-Type": "application/json" } });
};
