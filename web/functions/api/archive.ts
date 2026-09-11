/**
 * Take one fit into the archive.
 *
 * Written by the scheduled job, not by a browser: it carries a shared secret,
 * and there is exactly one thing it can do. A public archive that anyone could
 * write to would be an archive worth nothing.
 */

import { store, type Surface } from "../../lib/history";
import { fanOut } from "../../lib/fanout";

interface Env { DB: D1Database; ARCHIVE_SECRET: string }

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  if (!env.ARCHIVE_SECRET) return json(503, { error: "the archive is not configured" });

  const auth = request.headers.get("Authorization") || "";
  const given = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  // Constant-time on length and content, so a wrong secret leaks nothing by how
  // long the refusal took.
  let diff = given.length ^ env.ARCHIVE_SECRET.length;
  for (let i = 0; i < Math.max(given.length, env.ARCHIVE_SECRET.length); i++) {
    diff |= (given.charCodeAt(i) || 0) ^ (env.ARCHIVE_SECRET.charCodeAt(i) || 0);
  }
  if (diff !== 0) return json(403, { error: "refused" });

  let body: { generated_at?: string; engine?: string; surfaces?: Record<string, Surface> };
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "body is not valid JSON" });
  }
  const surfaces = body.surfaces || {};
  const stored: string[] = [];
  for (const currency of Object.keys(surfaces)) {
    const s = surfaces[currency];
    if (!s?.as_of || !s?.quality) continue;   // never archive a half-formed fit
    stored.push(await store(env.DB, s, body.engine ?? null));
    // Alerts go out after the fit is safely stored, and in the background: a
    // slow or dead receiver must not stop the archive from acknowledging.
    ctx.waitUntil(fanOut(env.DB, s as never));
  }
  return json(200, { stored: stored.length, ids: stored });
};
