/**
 * What the signed-in person may use.
 *
 * This lives beside the subscriber record rather than in the fit service,
 * because the record moved here with the webhook. Identity comes from the
 * session cookie; entitlement comes from the row.
 */

import { readCookie, verify } from "../../lib/token";
import { byEmail, byIdentity, entitlement } from "../../lib/subscribers";

interface Env { DB: D1Database; SESSION_SECRET: string }

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  if (!identity) {
    return new Response(JSON.stringify({ signed_in: false }), {
      status: 401, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  }
  // A subscription bound to this account wins, because somebody bound it
  // deliberately. Otherwise match the verified email, which covers the ordinary
  // case of paying and signing in as yourself.
  const record = (await byIdentity(ctx.env.DB, identity.sub))
              ?? (await byEmail(ctx.env.DB, identity.email));
  return new Response(JSON.stringify({
    signed_in: true, email: identity.email, name: identity.name ?? null,
    ...entitlement(record),
  }), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
};
