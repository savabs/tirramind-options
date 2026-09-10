import { describe, expect, it } from "vitest";
import { clearCookie, cookie, readCookie, sign, verify, SESSION_COOKIE } from "../lib/token";

const SECRET = "a-secret-that-is-long-enough-to-be-a-secret";
const now = 1_800_000_000;
const identity = { sub: "1234", email: "a@b.com", email_verified: true, name: "A",
                   iat: now, exp: now + 3600 };

describe("session tokens", () => {
  it("round-trips an identity", async () => {
    const t = await sign(identity, SECRET);
    expect(await verify(t, SECRET, now)).toEqual(identity);
  });

  it("refuses a token signed with another secret", async () => {
    const t = await sign(identity, "a-different-secret-entirely");
    expect(await verify(t, SECRET, now)).toBeNull();
  });

  it("refuses a tampered payload", async () => {
    const t = await sign(identity, SECRET);
    const [payload, sig] = t.split(".");
    const forged = btoa(JSON.stringify({ ...identity, email: "attacker@evil.com" }))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(await verify(`${forged}.${sig}`, SECRET, now)).toBeNull();
    expect(payload).not.toEqual(forged);
  });

  it("refuses an expired token", async () => {
    const t = await sign(identity, SECRET);
    expect(await verify(t, SECRET, identity.exp + 1)).toBeNull();
  });

  it("refuses a token that is not two parts", async () => {
    for (const junk of ["", "abc", "a.b.c", "."]) {
      expect(await verify(junk, SECRET, now)).toBeNull();
    }
  });

  it("refuses a validly signed token with no subject", async () => {
    const t = await sign({ ...identity, sub: "" }, SECRET);
    expect(await verify(t, SECRET, now)).toBeNull();
  });

  it("carries no algorithm field to confuse", async () => {
    const t = await sign(identity, SECRET);
    const decoded = atob(t.split(".")[0].replace(/-/g, "+").replace(/_/g, "/"));
    expect(decoded).not.toContain("alg");
    expect(decoded).not.toContain("none");
  });
});

describe("the cookie", () => {
  it("cannot be read by script and is not sent cross-site", () => {
    const c = cookie("tok");
    expect(c).toContain("HttpOnly");
    expect(c).toContain("Secure");
    expect(c).toContain("SameSite=Lax");
  });

  it("clears by expiring immediately", () => {
    expect(clearCookie()).toContain("Max-Age=0");
  });

  it("is found among other cookies", () => {
    expect(readCookie(`other=1; ${SESSION_COOKIE}=abc.def; third=2`)).toBe("abc.def");
    expect(readCookie("other=1")).toBeNull();
    expect(readCookie(null)).toBeNull();
  });
});
