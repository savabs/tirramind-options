/**
 * One archived surface, in full.
 *
 * This is the paid half. The series next door is free because it is the evidence;
 * the per-strike detail is the thing somebody would build on, and building on it
 * is what a subscription is for.
 */

import { readCookie, verify } from "../../../lib/token";
import { byEmail, byIdentity, entitlement } from "../../../lib/subscribers";

interface Env { DB: D1Database; SESSION_SECRET: string }

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const identity = await verify(readCookie(ctx.request.headers.get("Cookie")) || "",
                                ctx.env.SESSION_SECRET);
  if (!identity) return json(401, { error: "sign in to read an archived surface" });
  const record = (await byIdentity(ctx.env.DB, identity.sub))
              ?? (await byEmail(ctx.env.DB, identity.email));
  const ent = entitlement(record);
  if (!ent.entitled) {
    return json(403, { error: "an archived surface is part of the paid tier",
                       ...ent });
  }

  const id = decodeURIComponent(String(ctx.params.id));
  const row = await ctx.env.DB.prepare(
    "SELECT payload FROM surface_history WHERE id = ?").bind(id).first<{ payload: string }>();
  if (!row) return json(404, { error: "no fit with that id", id });
  return new Response(row.payload, {
    headers: { "Content-Type": "application/json",
               // An archived fit is immutable: it is what happened at an instant.
               "Cache-Control": "private, max-age=31536000, immutable" } });
};
