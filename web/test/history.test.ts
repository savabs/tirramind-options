/**
 * The archive's one derived number, and the row it stores.
 */
import { describe, expect, it } from "vitest";
import { atm30, rowFor, type Surface } from "../lib/history";

const slice = (expiry: string, dte: number, atmVol: number) => ({
  expiry, dte, forward: 78000, status: "ok", rmse_vol_pts: 0.4,
  log_moneyness: [-0.1, 0, 0.1],
  our_iv: [atmVol + 0.05, atmVol, atmVol + 0.04],
});

const surface = (expiries: ReturnType<typeof slice>[]): Surface => ({
  currency: "BTC", as_of: "2026-09-11T04:13:47+00:00", forward_front: 78000,
  quality: { rmse_vol_pts: 0.54, inside_bid_ask: 0.93, quotes_fitted: 412,
             expiries: expiries.length, our_butterfly_violations: 0,
             our_calendar_violations: 0, executable_venue_arbs: 0 },
  expiries,
});

describe("at the money at thirty days", () => {
  it("reads straight off an expiry that sits exactly there", () => {
    expect(atm30(surface([slice("a", 30, 0.5)]))).toBeCloseTo(0.5, 9);
  });

  it("interpolates between the two expiries either side", () => {
    const v = atm30(surface([slice("a", 7, 0.60), slice("b", 60, 0.40)]))!;
    expect(v).toBeLessThan(0.60);
    expect(v).toBeGreaterThan(0.40);
  });

  it("interpolates in total variance, not in volatility", () => {
    // Volatility-linear would give exactly the midpoint. Total variance is the
    // quantity that is linear in time; interpolating volatility across
    // maturities admits calendar arbitrage.
    const v = atm30(surface([slice("a", 10, 0.40), slice("b", 50, 0.60)]))!;
    expect(v).not.toBeCloseTo(0.5, 3);
    const w0 = 0.4 ** 2 * (10 / 365), w1 = 0.6 ** 2 * (50 / 365);
    const f = (30 / 365 - 10 / 365) / (50 / 365 - 10 / 365);
    expect(v).toBeCloseTo(Math.sqrt((w0 + f * (w1 - w0)) / (30 / 365)), 9);
  });

  it("clamps rather than extrapolating past the listed expiries", () => {
    expect(atm30(surface([slice("a", 200, 0.45)]))).toBeCloseTo(0.45, 9);
    expect(atm30(surface([slice("a", 1, 0.80)]))).toBeCloseTo(0.80, 9);
  });

  it("says nothing when there is nothing to say", () => {
    expect(atm30(surface([]))).toBeNull();
  });
});

describe("the archived row", () => {
  const r = rowFor(surface([slice("a", 30, 0.5)]), "voltorch 0.2.1");

  it("is keyed by currency and instant, so a retry replaces rather than repeats", () => {
    expect(r.id).toBe("BTC|2026-09-11T04:13:47+00:00");
  });

  it("carries the engine, because the fit is a property of it too", () => {
    expect(r.engine).toBe("voltorch 0.2.1");
  });

  it("keeps an epoch alongside the timestamp, for range queries", () => {
    expect(r.as_of_s).toBeCloseTo(Date.parse("2026-09-11T04:13:47+00:00") / 1000, 6);
  });

  it("lifts the quality numbers out of the payload", () => {
    expect(r.rmse_vol_pts).toBe(0.54);
    expect(r.quotes_fitted).toBe(412);
    expect(r.executable_arbs).toBe(0);
  });

  it("keeps the whole surface, not a summary of it", () => {
    expect(JSON.parse(r.payload).expiries[0].our_iv).toHaveLength(3);
  });
});
