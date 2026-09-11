/**
 * The archive: one row per fit, kept forever.
 *
 * The headline numbers and the full payload live in the same row because they
 * come from the same instant and answer two different questions. A time series
 * of fit quality and market level is what most readers want; the payload is what
 * somebody reconstructing a surface needs.
 */

export interface Surface {
  currency: string;
  as_of: string;
  forward_front: number;
  quality: {
    rmse_vol_pts: number; inside_bid_ask: number; quotes_fitted: number;
    expiries: number; our_butterfly_violations: number;
    our_calendar_violations: number; executable_venue_arbs: number;
  };
  expiries: Array<{
    expiry: string; dte: number; forward: number; status: string;
    rmse_vol_pts: number; log_moneyness: number[]; our_iv: number[];
  }>;
}

export interface Row {
  id: string; currency: string; as_of: string; as_of_s: number;
  engine: string | null; forward: number; rmse_vol_pts: number;
  inside_bid_ask: number; quotes_fitted: number; expiries: number;
  butterfly_violations: number; calendar_violations: number;
  executable_arbs: number; atm_30d: number | null;
}

/** Our fitted volatility at the forward, for one expiry. */
function atm(slice: Surface["expiries"][number]): number | null {
  const ks = slice.log_moneyness, iv = slice.our_iv;
  if (!ks?.length) return null;
  for (let i = 1; i < ks.length; i++) {
    if (ks[i - 1] <= 0 && ks[i] >= 0) {
      const w = (0 - ks[i - 1]) / ((ks[i] - ks[i - 1]) || 1);
      return iv[i - 1] + w * (iv[i] - iv[i - 1]);
    }
  }
  return null;
}

/**
 * One headline number per fit: at-the-money volatility at thirty days.
 *
 * Interpolated in total variance between the expiries either side, because
 * that is the quantity that is linear in time; interpolating volatility itself
 * across maturities admits calendar arbitrage. Returns null rather than a guess
 * when thirty days is not bracketed by two listed expiries.
 */
export function atm30(surface: Surface): number | null {
  const pts = surface.expiries
    .map((e) => ({ t: e.dte / 365, v: atm(e) }))
    .filter((p) => p.v !== null && p.t > 0)
    .sort((a, b) => a.t - b.t) as Array<{ t: number; v: number }>;
  if (pts.length === 0) return null;
  const target = 30 / 365;
  if (pts.length === 1 || target <= pts[0].t) return pts[0].v;
  if (target >= pts[pts.length - 1].t) return pts[pts.length - 1].v;
  for (let i = 1; i < pts.length; i++) {
    if (target <= pts[i].t) {
      const w0 = pts[i - 1].v ** 2 * pts[i - 1].t;
      const w1 = pts[i].v ** 2 * pts[i].t;
      const f = (target - pts[i - 1].t) / (pts[i].t - pts[i - 1].t);
      return Math.sqrt((w0 + f * (w1 - w0)) / target);
    }
  }
  return null;
}

export function rowFor(surface: Surface, engine: string | null): Row & { payload: string } {
  const q = surface.quality;
  return {
    id: `${surface.currency}|${surface.as_of}`,
    currency: surface.currency,
    as_of: surface.as_of,
    as_of_s: Date.parse(surface.as_of) / 1000,
    engine,
    forward: surface.forward_front,
    rmse_vol_pts: q.rmse_vol_pts,
    inside_bid_ask: q.inside_bid_ask,
    quotes_fitted: q.quotes_fitted,
    expiries: q.expiries,
    butterfly_violations: q.our_butterfly_violations,
    calendar_violations: q.our_calendar_violations,
    executable_arbs: q.executable_venue_arbs,
    atm_30d: atm30(surface),
    payload: JSON.stringify(surface),
  };
}

export async function store(db: D1Database, surface: Surface, engine: string | null) {
  const r = rowFor(surface, engine);
  // Re-archiving the same instant replaces rather than duplicates: a workflow
  // that retries must not put the same fit in the series twice.
  await db.prepare(
    `INSERT OR REPLACE INTO surface_history
       (id, currency, as_of, as_of_s, engine, forward, rmse_vol_pts, inside_bid_ask,
        quotes_fitted, expiries, butterfly_violations, calendar_violations,
        executable_arbs, atm_30d, payload)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(r.id, r.currency, r.as_of, r.as_of_s, r.engine, r.forward, r.rmse_vol_pts,
          r.inside_bid_ask, r.quotes_fitted, r.expiries, r.butterfly_violations,
          r.calendar_violations, r.executable_arbs, r.atm_30d, r.payload).run();
  return r.id;
}

export const COLUMNS =
  `id, currency, as_of, as_of_s, engine, forward, rmse_vol_pts, inside_bid_ask,
   quotes_fitted, expiries, butterfly_violations, calendar_violations,
   executable_arbs, atm_30d`;
