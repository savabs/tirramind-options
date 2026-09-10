/**
 * The shared rule table, from this side.
 *
 * Every case in rules.json must hold here and in the Python implementation.
 * A rule cannot be changed in one language without the other's tests failing,
 * which is the point: the two decide the same question about money.
 */
import { describe, expect, it } from "vitest";
import vectors from "../lib/rules.json";
import { decideAdjustment, effectiveActive, parseTierMap, resolveTier } from "../lib/rules";

describe("effective access", () => {
  for (const v of vectors.effective_active) {
    it(v.case, () => {
      expect(effectiveActive(v.entry as never, v.now)).toBe(v.expect);
    });
  }
});

describe("price to tier", () => {
  for (const v of vectors.tier) {
    it(v.case, () => {
      expect(resolveTier(v.price_id, v.map)).toBe(v.expect);
    });
  }
  it("skips junk rather than inventing a tier", () => {
    expect([...parseTierMap("pri_a:terminal,garbage,:,pri_b:").entries()])
      .toEqual([["pri_a", "terminal"]]);
  });
});

describe("refunds and chargebacks", () => {
  for (const v of vectors.adjustment) {
    it(v.case, () => {
      const d = decideAdjustment(v.action, v.status, v.kind);
      expect(d.revoke).toBe(v.expect_revoke);
      expect(d.applied).toBe(v.expect_applied);
    });
  }
});
