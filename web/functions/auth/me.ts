/**
 * Who is signed in. Identity only.
 *
 * This endpoint deliberately says nothing about what the person has paid for.
 * Entitlement is answered by the service that owns the subscriber record, using
 * the same rule that the webhook handler writes with; a second copy of that rule
 * here would be a second thing to keep correct about money.
 */

import { readCookie, verify } from "../../lib/token";

interface Env { SESSION_SECRET: string }

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  const body = identity
    ? { signed_in: true, email: identity.email, name: identity.name ?? null,
        sub: identity.sub, expires_at: identity.exp }
    : { signed_in: false };
  return new Response(JSON.stringify(body), {
    status: identity ? 200 : 401,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
};
