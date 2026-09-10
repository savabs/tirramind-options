/**
 * Session tokens: a signed statement of who someone is, and nothing else.
 *
 * Deliberately not a claim about what they have paid for. Entitlement lives in
 * one place, the Python subscriber store, where the rules about grace windows
 * and refunds are already written and tested. Putting a copy of those rules here
 * would mean two implementations of the money-correctness logic drifting apart,
 * and the one that drifts is the one nobody is watching.
 *
 * Format is `payload.signature`, both base64url, signature is HMAC-SHA256 over
 * the payload bytes. Not a JWT: no algorithm field, so there is no algorithm to
 * confuse, and no library to keep up to date.
 */

export interface Identity {
  /** Google's stable subject id. The user's email can change; this cannot. */
  sub: string;
  email: string;
  email_verified: boolean;
  name?: string;
  /** Seconds since the epoch. */
  iat: number;
  exp: number;
}

const enc = new TextEncoder();
const dec = new TextDecoder();

function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array {
  const pad = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(pad + "=".repeat((4 - (pad.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function key(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign", "verify"]);
}

export async function sign(identity: Identity, secret: string): Promise<string> {
  const payload = b64urlEncode(enc.encode(JSON.stringify(identity)));
  const sig = await crypto.subtle.sign("HMAC", await key(secret), enc.encode(payload));
  return `${payload}.${b64urlEncode(new Uint8Array(sig))}`;
}

/** Returns the identity, or null for anything that is not a valid, live token. */
export async function verify(token: string, secret: string, now = Date.now() / 1000):
  Promise<Identity | null> {
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [payload, sig] = parts;
  let ok = false;
  try {
    // Constant-time by construction: crypto.subtle.verify does not short-circuit.
    ok = await crypto.subtle.verify("HMAC", await key(secret), b64urlDecode(sig),
      enc.encode(payload));
  } catch {
    return null;
  }
  if (!ok) return null;
  let identity: Identity;
  try {
    identity = JSON.parse(dec.decode(b64urlDecode(payload)));
  } catch {
    return null;
  }
  if (typeof identity.exp !== "number" || identity.exp <= now) return null;
  if (!identity.sub || !identity.email) return null;
  return identity;
}

export const SESSION_COOKIE = "tmo_session";
export const SESSION_TTL_S = 60 * 60 * 12;

export function cookie(token: string, maxAge: number = SESSION_TTL_S): string {
  // HttpOnly so script cannot read it; Lax so the redirect back from Google
  // still carries it; Secure because everything here is https.
  return `${SESSION_COOKIE}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`;
}

export function clearCookie(): string {
  return `${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`;
}

export function readCookie(header: string | null): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === SESSION_COOKIE) return v.join("=") || null;
  }
  return null;
}
