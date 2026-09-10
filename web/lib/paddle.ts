/**
 * Verify that a webhook really came from Paddle.
 *
 * Header is `ts=<unix seconds>;h1=<hex>`, and the signed message is the
 * timestamp, a colon, and the raw body exactly as received. Any reserialisation
 * of the body before this runs breaks the signature, which is why the caller
 * passes the string it read and not a parsed object.
 */

const enc = new TextEncoder();

export class WebhookError extends Error {}

/** Paddle's own tolerance is five seconds. This is wider on purpose: a
 *  legitimate delivery arriving late is a paying customer who does not get
 *  provisioned, which is the same class of wrong as accepting a forgery. The
 *  event ledger is the real replay defence; this is depth behind it. */
export const MAX_AGE_S = 300;

function parseHeader(header: string): { ts: string; h1: string } {
  const out: Record<string, string> = {};
  for (const part of (header || "").split(";")) {
    const at = part.indexOf("=");
    if (at > 0) out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  if (!out.ts || !out.h1) throw new WebhookError("missing ts or h1 in the signature header");
  return { ts: out.ts, h1: out.h1 };
}

const hex = (buf: ArrayBuffer) =>
  [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** Constant-time compare, so a wrong signature leaks nothing by how long it took. */
function equal(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function verifySignature(rawBody: string, header: string, secret: string,
                                      now = Date.now() / 1000): Promise<void> {
  if (!secret) throw new WebhookError("no endpoint secret configured");
  const { ts, h1 } = parseHeader(header);
  const t = Number(ts);
  if (!Number.isFinite(t)) throw new WebhookError("the signature timestamp is not a number");
  if (Math.abs(now - t) > MAX_AGE_S) throw new WebhookError("the signature timestamp is stale");

  const key = await crypto.subtle.importKey("raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`${ts}:${rawBody}`));
  if (!equal(hex(mac), h1)) throw new WebhookError("the webhook signature does not match");
}


/**
 * Ask Paddle for a customer's email.
 *
 * No subscription or transaction webhook carries an email address; Paddle's
 * Customer object is the only source. That matters here more than it did on the
 * box, because sign-in matches a verified Google address against this field: a
 * record with no email is a paying customer who cannot be recognised when they
 * log in.
 *
 * Best effort by design. A Paddle hiccup must not block an activation, since a
 * subscriber with a working key and no email on file is recoverable, and one who
 * was never provisioned at all is a support ticket.
 */
export async function fetchCustomerEmail(customerId: string, apiKey: string, mode: string,
                                         fetchImpl: typeof fetch = fetch): Promise<string | null> {
  if (!customerId || !apiKey) return null;
  const base = mode === "live" ? "https://api.paddle.com" : "https://sandbox-api.paddle.com";
  try {
    const r = await fetchImpl(`${base}/customers/${customerId}`, {
      headers: { Authorization: `Bearer ${apiKey}`, "User-Agent": "tirramind-options/0.1" },
    });
    if (!r.ok) return null;
    const email = ((await r.json()) as any)?.data?.email;
    return typeof email === "string" && email.trim() ? email.trim().toLowerCase() : null;
  } catch {
    return null;
  }
}
