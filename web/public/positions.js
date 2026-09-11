/**
 * Positions, priced on our surface rather than the venue's marks.
 *
 * The key never leaves the browser. Deribit accepts cross-origin calls, so the
 * page talks to it directly: we never see the credential, never store it, never
 * transit it, and there is no database of other people's trading keys to lose.
 * That is worth more than the convenience of doing it server-side.
 *
 * The trade is honest about its own risk: the secret sits in localStorage, which
 * any script running on this origin could read. This page loads no third-party
 * script and no CDN, which is why that is an acceptable place to put it, and why
 * it must stay that way. There is a "do not remember" option for anyone who
 * would rather paste it each time.
 *
 * Only read scope is ever needed. A key with trade or withdraw scope will work
 * and should not be used; the setup text says so.
 */

const DERIBIT = "https://www.deribit.com/api/v2";
const STORE_KEY = "tmo_deribit";

// ── Black-76, undiscounted (r = 0), differentiated by hand ────────────────
// Abramowitz and Stegun 7.1.26. Enough for greeks that are read on a screen;
// the engine's own greeks come from autograd and agree with closed form to 1e-9.
function erf(x) {
  const s = Math.sign(x); x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t
    - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}
const N = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
const n = (x) => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);

export function greeks(F, K, T, sigma, isCall) {
  if (!(F > 0 && K > 0 && T > 0 && sigma > 0)) return null;
  const sq = sigma * Math.sqrt(T);
  const d1 = (Math.log(F / K) + 0.5 * sigma * sigma * T) / sq;
  const d2 = d1 - sq;
  const price = isCall ? F * N(d1) - K * N(d2) : K * N(-d2) - F * N(-d1);
  return {
    price,
    // With respect to the forward, because the hedge instrument here is a future.
    delta: isCall ? N(d1) : N(d1) - 1,
    gamma: n(d1) / (F * sq),
    // Per one volatility point, and per one day, which is how they are read.
    vega: (F * n(d1) * Math.sqrt(T)) / 100,
    theta: (-F * n(d1) * sigma) / (2 * Math.sqrt(T)) / 365,
  };
}

/** Our fitted volatility at this strike and expiry, interpolated in log-moneyness. */
export function ourVol(surface, expiry, strike) {
  const slice = surface.expiries.find((e) => e.expiry === expiry);
  if (!slice) return null;
  const k = Math.log(strike / slice.forward);
  const ks = slice.log_moneyness, iv = slice.our_iv;
  if (k <= ks[0]) return iv[0];
  if (k >= ks[ks.length - 1]) return iv[iv.length - 1];
  for (let i = 1; i < ks.length; i++) {
    if (k <= ks[i]) {
      const w = (k - ks[i - 1]) / ((ks[i] - ks[i - 1]) || 1);
      return iv[i - 1] + w * (iv[i] - iv[i - 1]);
    }
  }
  return null;
}

/**
 * An instrument name to its parts.
 *
 * Two shapes, because Deribit runs two books. The coin-settled one is
 * `BTC-18SEP26-78000-C`. The USDC one is `AVAX_USDC-12SEP26-6d5-C`, where the
 * underlying carries its quote currency and the strike writes its decimal point
 * as a `d`, because a dot would collide with the separator. Reading 6d5 as
 * anything but 6.5 silently prices the wrong strike.
 *
 * Futures and perpetuals have no strike.
 */
export function parseInstrument(name) {
  const p = String(name).split("-");
  if (p.length < 4) return { currency: String(p[0]).split("_")[0], kind: "future", name };
  const [rawCurrency, date, rawStrike, cp] = p;
  const currency = rawCurrency.split("_")[0];
  const strike = rawStrike.replace(/d/i, ".");
  const m = /^(\d{1,2})([A-Z]{3})(\d{2})$/.exec(date);
  const months = { JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
                   JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11 };
  if (!m || !(m[2] in months)) return { currency, kind: "option", name };
  const d = new Date(Date.UTC(2000 + +m[3], months[m[2]], +m[1], 8, 0, 0));
  return { currency, kind: "option", name, strike: Number(strike),
           isCall: cp === "C", expiry: d.toISOString().slice(0, 10), expiryMs: d.getTime() };
}

// ── the venue ─────────────────────────────────────────────────────────────
export const credentials = {
  load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch { return null; }
  },
  save(v) { try { localStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch { /* private mode */ } },
  clear() { try { localStorage.removeItem(STORE_KEY); } catch { /* nothing to do */ } },
};

async function rpc(path, params, token) {
  const url = new URL(DERIBIT + path);
  for (const k in params) url.searchParams.set(k, params[k]);
  const r = await fetch(url, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined);
  const body = await r.json();
  if (body.error) {
    const reason = body.error.data?.reason || body.error.message || "refused";
    throw new Error(`Deribit: ${reason}`);
  }
  return body.result;
}

export async function authenticate(clientId, clientSecret) {
  const r = await rpc("/public/auth", { grant_type: "client_credentials",
    client_id: clientId, client_secret: clientSecret });
  return { token: r.access_token, scope: r.scope || "" };
}

export async function positions(token, currency) {
  const rows = await rpc("/private/get_positions", { currency, kind: "any" }, token);
  return (rows || []).filter((p) => Number(p.size) !== 0);
}

/**
 * Our view of a book: every position priced on our surface, not the venue's.
 *
 * A position whose expiry we do not fit is carried through and flagged rather
 * than dropped. Silently omitting a leg would understate the risk of the book,
 * which is the one mistake a position screen must never make.
 */
export function analyse(rows, surfaces) {
  const out = [], totals = { delta: 0, gamma: 0, vega: 0, theta: 0, pnl: 0 };
  const now = Date.now();
  for (const p of rows) {
    const inst = parseInstrument(p.instrument_name);
    const surface = surfaces[inst.currency];
    const size = Number(p.size);
    const row = { ...inst, size, mark: Number(p.mark_price),
                  average: Number(p.average_price), venueDelta: Number(p.delta),
                  pnl: Number(p.floating_profit_loss ?? 0), ours: null, note: null };

    if (inst.kind === "future") {
      row.ours = { delta: 1, gamma: 0, vega: 0, theta: 0 };
      totals.delta += size > 0 ? 1 * Math.sign(size) * Math.abs(size) : -Math.abs(size);
      out.push(row); totals.pnl += row.pnl; continue;
    }
    if (!surface) { row.note = `no surface for ${inst.currency}`; out.push(row); continue; }
    const slice = surface.expiries.find((e) => e.expiry === inst.expiry);
    if (!slice) {
      row.note = "this expiry is not in the current fit";
      out.push(row); totals.pnl += row.pnl; continue;
    }
    const T = Math.max((inst.expiryMs - now) / (365 * 864e5), 1e-6);
    const sigma = ourVol(surface, inst.expiry, inst.strike);
    const g = greeks(slice.forward, inst.strike, T, sigma, inst.isCall);
    if (!g) { row.note = "could not price this leg"; out.push(row); continue; }
    row.ours = g; row.ourVol = sigma; row.forward = slice.forward;
    row.venueVol = null;
    const at = slice.strike.indexOf(inst.strike);
    if (at >= 0) row.venueVol = slice.venue_mark_iv[at];
    for (const k of ["delta", "gamma", "vega", "theta"]) totals[k] += size * g[k];
    totals.pnl += row.pnl;
    out.push(row);
  }
  return { rows: out, totals };
}
