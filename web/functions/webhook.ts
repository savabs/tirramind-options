/**
 * The Paddle webhook, at the edge.
 *
 * It moved here from the box because billing that depends on one machine being
 * up is billing that stops when that machine stops, and a refund that never
 * reaches us is a customer who keeps access they have been repaid for.
 *
 * Order matters and is the same lesson the previous implementation learned the
 * hard way: cap the body before reading it, verify the signature before
 * parsing, and record the event before acting on it.
 */

import { fetchCustomerEmail, verifySignature, WebhookError } from "../lib/paddle";
import { apply } from "../lib/subscribers";

interface Env {
  DB: D1Database;
  PADDLE_WEBHOOK_SECRET: string;
  TMO_TIER_PRICE_MAP: string;
  /** Optional. Without it a subscriber is provisioned with no email on file,
   *  and sign-in cannot recognise them until they link a key by hand. */
  PADDLE_API_KEY?: string;
  PADDLE_MODE?: string;
}

/** Paddle payloads run to a few KB. This is headroom, not a guess at the size. */
const MAX_BODY_BYTES = 256 * 1024;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  if (!env.PADDLE_WEBHOOK_SECRET) return json(503, { ok: false, error: "not configured" });

  const declared = Number(request.headers.get("Content-Length") ?? NaN);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    return json(413, { ok: false, error: "request body too large" });
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return json(413, { ok: false, error: "request body too large" });

  try {
    await verifySignature(raw, request.headers.get("Paddle-Signature") || "",
                          env.PADDLE_WEBHOOK_SECRET);
  } catch (e) {
    // 400, not 401: Paddle retries on 5xx and gives up on 4xx, and a forgery
    // should not earn retries.
    return json(400, { ok: false, error: e instanceof WebhookError ? e.message : "rejected" });
  }

  let event: Record<string, any>;
  try {
    event = JSON.parse(raw);
  } catch {
    return json(400, { ok: false, error: "body is not valid JSON" });
  }

  const eventId = event.event_id ? String(event.event_id) : null;
  const now = Date.now() / 1000;

  // The replay guard. Paddle reuses an event id on retries, so this makes a
  // retry a no-op rather than a double write, and refuses a captured webhook
  // replayed inside the signature's window. A payload with no id cannot be
  // deduplicated, so it is processed rather than silently dropped.
  if (eventId) {
    const seen = await env.DB.prepare("SELECT applied FROM events WHERE event_id = ?")
      .bind(eventId).first();
    if (seen) {
      return json(200, { ok: true, handled: false, reason: "duplicate event", event_id: eventId });
    }
  }

  let result;
  try {
    result = await apply(env.DB, event, env.TMO_TIER_PRICE_MAP || "", now,
      (customerId) => fetchCustomerEmail(customerId, env.PADDLE_API_KEY || "",
                                         env.PADDLE_MODE || "sandbox"));
  } catch (e) {
    // Record the failure and ask Paddle to retry: losing an event silently is
    // worse than being asked again.
    await env.DB.prepare(
      `INSERT OR REPLACE INTO events (event_id, event_type, occurred_at, received_at, applied, reason, payload)
       VALUES (?, ?, ?, ?, 0, ?, ?)`)
      .bind(eventId ?? crypto.randomUUID(), event.event_type ?? null, event.occurred_at ?? null,
            now, `error: ${e instanceof Error ? e.message : String(e)}`, raw.slice(0, 20000)).run();
    return json(500, { ok: false, error: "could not apply the event" });
  }

  await env.DB.prepare(
    `INSERT OR REPLACE INTO events (event_id, event_type, occurred_at, received_at, applied, reason, payload)
     VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .bind(eventId ?? crypto.randomUUID(), event.event_type ?? null, event.occurred_at ?? null,
          now, result.handled ? 1 : 0, result.reason, raw.slice(0, 20000)).run();

  // The minted key never goes back to Paddle: it has no use for it, and there is
  // no reason to put a customer's credential on that wire.
  return json(200, { ok: true, ...result });
};
