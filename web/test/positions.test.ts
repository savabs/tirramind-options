/**
 * Positions priced on our surface. Pure functions only: no network, no key.
 */
import { describe, expect, it } from "vitest";
import { analyse, greeks, ourVol, parseInstrument } from "../public/positions.js";

const surface = {
  expiries: [{
    expiry: "2026-09-18", dte: 8, forward: 78000, quotes: 3, rmse_vol_pts: 0.4,
    inside_bid_ask: 1, status: "ok",
    strike: [70000, 78000, 86000],
    log_moneyness: [Math.log(70000 / 78000), 0, Math.log(86000 / 78000)],
    bid_iv: [0.55, 0.49, 0.53], ask_iv: [0.57, 0.51, 0.55],
    venue_mark_iv: [0.56, 0.50, 0.54], our_iv: [0.561, 0.502, 0.541],
  }],
};

describe("Black-76 greeks", () => {
  const g = greeks(100, 100, 1, 0.2, true)!;
  it("puts an at-the-money call near a half delta", () => {
    expect(g.delta).toBeGreaterThan(0.5);
    expect(g.delta).toBeLessThan(0.56);
  });
  it("gives a put the call's delta less one", () => {
    const p = greeks(100, 100, 1, 0.2, false)!;
    expect(p.delta).toBeCloseTo(g.delta - 1, 12);
  });
  it("satisfies put-call parity on the forward", () => {
    const c = greeks(100, 90, 1, 0.3, true)!, p = greeks(100, 90, 1, 0.3, false)!;
    expect(c.price - p.price).toBeCloseTo(100 - 90, 8);
  });
  it("prices deep in the money at its intrinsic, and deep out at nothing", () => {
    expect(greeks(100, 1, 1, 0.2, true)!.price).toBeCloseTo(99, 0);
    expect(greeks(100, 1000, 1, 0.2, true)!.price).toBeLessThan(0.01);
  });
  it("makes vega and gamma positive and theta negative for a long option", () => {
    expect(g.vega).toBeGreaterThan(0);
    expect(g.gamma).toBeGreaterThan(0);
    expect(g.theta).toBeLessThan(0);
  });
  it("refuses nonsense rather than returning a number", () => {
    for (const bad of [greeks(0, 100, 1, 0.2, true), greeks(100, 100, 0, 0.2, true),
                       greeks(100, 100, 1, 0, true), greeks(100, 100, -1, 0.2, true)]) {
      expect(bad).toBeNull();
    }
  });
});

describe("instrument names", () => {
  it("reads an option", () => {
    const i = parseInstrument("BTC-18SEP26-78000-C");
    expect(i).toMatchObject({ currency: "BTC", kind: "option", strike: 78000,
                              isCall: true, expiry: "2026-09-18" });
  });
  it("reads a put", () => {
    expect(parseInstrument("ETH-18SEP26-3000-P").isCall).toBe(false);
  });
  it("knows a perpetual is not an option", () => {
    expect(parseInstrument("BTC-PERPETUAL").kind).toBe("future");
  });
  it("does not invent a date it cannot read", () => {
    expect(parseInstrument("BTC-NOTADATE-1-C").strike).toBeUndefined();
  });
});

describe("our volatility at a strike", () => {
  it("returns the fitted value at a listed strike", () => {
    expect(ourVol(surface, "2026-09-18", 78000)).toBeCloseTo(0.502, 6);
  });
  it("interpolates between two strikes", () => {
    const v = ourVol(surface, "2026-09-18", 74000);
    expect(v).toBeGreaterThan(0.502);
    expect(v).toBeLessThan(0.561);
  });
  it("clamps outside the quoted range rather than extrapolating", () => {
    expect(ourVol(surface, "2026-09-18", 10000)).toBeCloseTo(0.561, 6);
    expect(ourVol(surface, "2026-09-18", 900000)).toBeCloseTo(0.541, 6);
  });
  it("has nothing to say about an expiry it did not fit", () => {
    expect(ourVol(surface, "2099-01-01", 78000)).toBeNull();
  });
});

describe("a book", () => {
  const rows = [{ instrument_name: "BTC-18SEP26-78000-C", size: 2,
                  mark_price: 0.03, average_price: 0.02, delta: 0.5,
                  floating_profit_loss: 0.02 }];

  it("prices a leg on our surface and adds it to the totals", () => {
    const { rows: out, totals } = analyse(rows, { BTC: surface });
    expect(out[0].ours).not.toBeNull();
    expect(out[0].ourVol).toBeCloseTo(0.502, 6);
    expect(totals.delta).toBeCloseTo(2 * out[0].ours!.delta, 12);
    expect(totals.vega).toBeGreaterThan(0);
  });

  it("shows the difference against the venue's own mark", () => {
    const { rows: out } = analyse(rows, { BTC: surface });
    expect(out[0].venueVol).toBeCloseTo(0.50, 6);
  });

  it("carries a leg it cannot price, flagged, instead of dropping it", () => {
    // Dropping a leg understates the risk of the book, which is the one thing a
    // position screen must never do.
    const { rows: out } = analyse(
      [{ instrument_name: "BTC-25DEC27-90000-C", size: 5, mark_price: 0.1,
         average_price: 0.1, delta: 0.4, floating_profit_loss: 0 }], { BTC: surface });
    expect(out).toHaveLength(1);
    expect(out[0].ours).toBeNull();
    expect(out[0].note).toMatch(/not in the current fit/);
  });

  it("says so when it has no surface for the currency at all", () => {
    const { rows: out } = analyse(rows, {});
    expect(out[0].note).toMatch(/no surface for BTC/);
  });

  it("ignores nothing and totals the profit and loss", () => {
    const { totals } = analyse(rows, { BTC: surface });
    expect(totals.pnl).toBeCloseTo(0.02, 12);
  });
});

describe("the USDC book's instrument names", () => {
  it("reads the underlying out of a quote-carrying name", () => {
    expect(parseInstrument("AVAX_USDC-12SEP26-6d5-C").currency).toBe("AVAX");
  });

  it("reads a strike whose decimal point is written as a d", () => {
    // 6d5 is 6.5. Reading it as anything else silently prices the wrong strike.
    expect(parseInstrument("AVAX_USDC-12SEP26-6d5-C").strike).toBe(6.5);
    expect(parseInstrument("XRP_USDC-12SEP26-2d75-P").strike).toBe(2.75);
  });

  it("still reads a whole-number strike in the same book", () => {
    expect(parseInstrument("SOL_USDC-12SEP26-200-C").strike).toBe(200);
  });

  it("leaves the coin-settled book exactly as it was", () => {
    expect(parseInstrument("BTC-18SEP26-78000-C")).toMatchObject(
      { currency: "BTC", strike: 78000, isCall: true, expiry: "2026-09-18" });
  });

  it("finds the underlying on a USDC perpetual too", () => {
    expect(parseInstrument("SOL_USDC-PERPETUAL")).toMatchObject(
      { currency: "SOL", kind: "future" });
  });
});
