/**
 * Alerts: what counts as the same violation, who gets told, and what is refused.
 */
import { describe, expect, it, vi } from "vitest";
import {
  arbPayload, checkUrl, deliver, fingerprint, matches, sign,
  type Arb, type Subscription,
} from "../lib/alerts";

const arb = (over: Partial<Arb> = {}): Arb =>
  ({ kind: "butterfly", expiry: "2026-09-18", dte: 7, edge_usd: 42.5,
     strikes: [70000, 78000, 86000], ...over });

const sub = (over: Partial<Subscription> = {}): Subscription =>
  ({ id: "a1", subscription_id: "sub_1", kind: "executable_arb",
     url: "https://example.com/hook", secret: "s3cret", min_edge_usd: 0,
     currency: null, ...over });

describe("what counts as the same violation", () => {
  it("is the same across refits when the structure is", () => {
    expect(fingerprint("BTC", arb())).toBe(fingerprint("BTC", arb()));
  });

  it("ignores the edge, because the same structure is worth a little more each tick", () => {
    // Keying on the amount would make every refit a new alert, which is how an
    // alerting system teaches people to ignore it.
    expect(fingerprint("BTC", arb({ edge_usd: 42.5 })))
      .toBe(fingerprint("BTC", arb({ edge_usd: 91.2 })));
  });

  it("does not confuse two currencies, expiries or structures", () => {
    const base = fingerprint("BTC", arb());
    expect(fingerprint("ETH", arb())).not.toBe(base);
    expect(fingerprint("BTC", arb({ expiry: "2026-10-02" }))).not.toBe(base);
    expect(fingerprint("BTC", arb({ kind: "calendar" }))).not.toBe(base);
    expect(fingerprint("BTC", arb({ strikes: [70000, 78000] }))).not.toBe(base);
  });

  it("does not care what order the strikes arrive in", () => {
    expect(fingerprint("BTC", arb({ strikes: [86000, 70000, 78000] })))
      .toBe(fingerprint("BTC", arb()));
  });
});

describe("who gets told", () => {
  it("respects a minimum edge", () => {
    expect(matches(sub({ min_edge_usd: 100 }), "BTC", arb({ edge_usd: 42.5 }))).toBe(false);
    expect(matches(sub({ min_edge_usd: 40 }), "BTC", arb({ edge_usd: 42.5 }))).toBe(true);
  });

  it("reports a violation whose size is unknown", () => {
    // Unknown size is not zero size, and suppressing it would hide exactly the
    // case worth looking at by hand.
    expect(matches(sub({ min_edge_usd: 1000 }), "BTC", arb({ edge_usd: null }))).toBe(true);
  });

  it("respects a currency filter, and no filter means every currency", () => {
    expect(matches(sub({ currency: "ETH" }), "BTC", arb())).toBe(false);
    expect(matches(sub({ currency: "BTC" }), "BTC", arb())).toBe(true);
    expect(matches(sub(), "ETH", arb())).toBe(true);
  });
});

describe("the webhook URL somebody supplies", () => {
  it("takes an ordinary https address", () => {
    expect(checkUrl("https://example.com/hook")).toEqual(
      { ok: true, url: "https://example.com/hook" });
  });

  it("refuses plain http, because a signed alert should not travel in the clear", () => {
    expect(checkUrl("http://example.com/hook")).toMatchObject({ ok: false });
  });

  it("refuses anything on the inside of a network", () => {
    // We make this request on their behalf, so it must not become a way to
    // probe what is reachable from our side.
    for (const u of ["https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.1/x",
                     "https://192.168.1.1/x", "https://169.254.169.254/latest/meta-data",
                     "https://172.16.0.1/x", "https://thing.internal/x"]) {
      expect(checkUrl(u), u).toMatchObject({ ok: false });
    }
  });

  it("refuses something that is not a URL at all", () => {
    expect(checkUrl("not a url")).toMatchObject({ ok: false, why: "that is not a URL" });
  });
});

describe("delivery", () => {
  it("signs the body so a receiver can verify it is us", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const fake = (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response("ok", { status: 200 });
    }) as unknown as typeof fetch;
    const payload = arbPayload(
      { currency: "BTC", as_of: "2026-09-11T07:00:00+00:00",
        quality: { executable_venue_arbs: 1, our_butterfly_violations: 0,
                   our_calendar_violations: 0, rmse_vol_pts: 0.5 } },
      [arb()]);
    const out = await deliver(sub(), payload, fake);
    expect(out.ok).toBe(true);
    const header = (seen[0].init.headers as Record<string, string>)["TirraMind-Signature"];
    expect(header).toMatch(/^ts=\d+;h1=[0-9a-f]{64}$/);
    const ts = Number(header.split(";")[0].slice(3));
    expect(header).toBe(await sign(seen[0].init.body as string, "s3cret", ts));
  });

  it("reports a refusal rather than raising", async () => {
    const fake = (async () => new Response("nope", { status: 500 })) as unknown as typeof fetch;
    const out = await deliver(sub(), { kind: "x", as_of: "", currency: "BTC",
      items: [], terminal: "" }, fake);
    expect(out).toMatchObject({ ok: false, status: 500, detail: "nope" });
  });

  it("survives a receiver that is simply gone", async () => {
    const fake = (async () => { throw new Error("connection refused"); }) as unknown as typeof fetch;
    const out = await deliver(sub(), { kind: "x", as_of: "", currency: "BTC",
      items: [], terminal: "" }, fake);
    expect(out.ok).toBe(false);
    expect(out.detail).toMatch(/connection refused/);
  });
});
