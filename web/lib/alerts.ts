/**
 * Telling somebody when there is money on the table.
 *
 * The monitor has been running every thirty minutes since yesterday and nobody
 * is watching the page at three in the morning, which is exactly when a book
 * goes wrong. This is the part that reaches out.
 *
 * Two rules shape the whole thing. An alert that repeats itself is an alert
 * people turn off, so the same violation persisting across refits is reported
 * once. And an alert that silently stops arriving is worse than no alerts at
 * all, because the reader believes the book is clean when nobody is looking, so
 * every attempt is logged whether it worked or not.
 */

export interface Arb {
  kind: string;
  expiry: string;
  dte: number;
  edge_usd: number | null;
  strikes: number[];
}

export interface Surface {
  currency: string;
  as_of: string;
  quality: {
    executable_venue_arbs: number;
    our_butterfly_violations: number;
    our_calendar_violations: number;
    rmse_vol_pts: number;
  };
  arbs?: Arb[];
}

export interface Subscription {
  id: string;
  subscription_id: string;
  kind: string;
  url: string;
  secret: string;
  min_edge_usd: number;
  currency: string | null;
}

/** How long the same violation stays quiet after being reported once. */
export const QUIET_S = 6 * 3600;

/**
 * What identifies "the same arbitrage" across refits.
 *
 * Deliberately not the edge: the same structure will be worth slightly
 * different amounts each time the book moves, and keying on the amount would
 * make every refit a new alert. Strikes are rounded for the same reason.
 */
export function fingerprint(currency: string, a: Arb): string {
  const strikes = (a.strikes || []).map((k) => Math.round(k)).sort((x, y) => x - y).join("/");
  return `${currency}:${a.kind}:${a.expiry}:${strikes}`;
}

export function matches(sub: Subscription, currency: string, a: Arb): boolean {
  if (sub.currency && sub.currency !== currency) return false;
  // A violation with no stated edge is reported: unknown size is not zero size,
  // and suppressing it would hide exactly the case worth looking at by hand.
  if (a.edge_usd === null || a.edge_usd === undefined) return true;
  return a.edge_usd >= (sub.min_edge_usd || 0);
}

export interface Payload {
  kind: string;
  as_of: string;
  currency: string;
  items: unknown[];
  terminal: string;
}

export function arbPayload(surface: Surface, arbs: Arb[]): Payload {
  return {
    kind: "executable_arb",
    as_of: surface.as_of,
    currency: surface.currency,
    items: arbs.map((a) => ({ kind: a.kind, expiry: a.expiry, dte: a.dte,
                              edge_usd: a.edge_usd, strikes: a.strikes })),
    terminal: "https://tirramind-options.pages.dev/terminal",
  };
}

export function integrityPayload(surface: Surface): Payload {
  const q = surface.quality;
  return {
    kind: "surface_integrity",
    as_of: surface.as_of,
    currency: surface.currency,
    items: [{ butterfly_violations: q.our_butterfly_violations,
              calendar_violations: q.our_calendar_violations,
              rmse_vol_pts: q.rmse_vol_pts }],
    terminal: "https://tirramind-options.pages.dev/terminal",
  };
}

const enc = new TextEncoder();
const hex = (b: ArrayBuffer) =>
  [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

/** Sign a delivery the way Paddle signs one, so a receiver can verify it is us. */
export async function sign(body: string, secret: string, ts: number): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return `ts=${ts};h1=${hex(await crypto.subtle.sign("HMAC", key, enc.encode(`${ts}:${body}`)))}`;
}

export async function deliver(sub: Subscription, payload: Payload,
                              fetchImpl: typeof fetch = fetch):
  Promise<{ ok: boolean; status: number | null; detail: string }> {
  const body = JSON.stringify(payload);
  const ts = Math.floor(Date.now() / 1000);
  try {
    const r = await fetchImpl(sub.url, {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "TirraMind-Signature": await sign(body, sub.secret, ts),
                 "User-Agent": "tirramind-options-alerts/1.0" },
      body,
      // A slow receiver must not hold the archive step open.
      signal: AbortSignal.timeout(8000),
    });
    return { ok: r.ok, status: r.status,
             detail: r.ok ? "delivered" : (await r.text()).slice(0, 200) };
  } catch (e) {
    return { ok: false, status: null,
             detail: e instanceof Error ? e.message.slice(0, 200) : "failed" };
  }
}

/**
 * A webhook URL somebody else supplies is a request we will make on their
 * behalf, so it is checked before it is stored: https only, and not pointed at
 * anything on the inside of a network.
 */
export function checkUrl(raw: string): { ok: true; url: string } | { ok: false; why: string } {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return { ok: false, why: "that is not a URL" };
  }
  if (u.protocol !== "https:") return { ok: false, why: "the URL must be https" };
  const host = u.hostname.toLowerCase();
  const blocked = host === "localhost" || host.endsWith(".localhost")
    || /^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(host)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(host)
    || host === "[::1]" || host.endsWith(".internal");
  if (blocked) return { ok: false, why: "that address is not reachable from the internet" };
  return { ok: true, url: u.toString() };
}
