/**
 * The series: what was fitted, when, and how good it was.
 *
 * Free, deliberately. It is the evidence for the claim the whole product makes,
 * and evidence behind a paywall persuades nobody. The full per-strike payload is
 * the paid part; this says what exists and how well it fitted.
 */

import { COLUMNS } from "../../../lib/history";

interface Env { DB: D1Database }

const MAX_ROWS = 2000;

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const url = new URL(ctx.request.url);
  const currency = (url.searchParams.get("currency") || "").toUpperCase();
  const from = Number(url.searchParams.get("from") ?? 0);
  const to = Number(url.searchParams.get("to") ?? Date.now() / 1000);
  const limit = Math.min(Number(url.searchParams.get("limit") ?? 500) || 500, MAX_ROWS);

  const where = ["as_of_s >= ?", "as_of_s <= ?"];
  const binds: unknown[] = [Number.isFinite(from) ? from : 0,
                            Number.isFinite(to) ? to : Date.now() / 1000];
  if (currency) { where.push("currency = ?"); binds.push(currency); }

  const { results } = await ctx.env.DB.prepare(
    `SELECT ${COLUMNS} FROM surface_history
      WHERE ${where.join(" AND ")} ORDER BY as_of_s DESC LIMIT ?`)
    .bind(...binds, limit).all();

  const span = await ctx.env.DB.prepare(
    `SELECT currency, COUNT(*) AS fits, MIN(as_of) AS first, MAX(as_of) AS last
       FROM surface_history GROUP BY currency`).all();

  return new Response(JSON.stringify({
    coverage: span.results,
    count: results.length,
    truncated: results.length === limit,
    fits: results,
  }), { headers: { "Content-Type": "application/json",
                   "Cache-Control": "public, max-age=60" } });
};
