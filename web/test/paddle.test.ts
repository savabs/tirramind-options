import { describe, expect, it } from "vitest";
import { verifySignature, WebhookError, MAX_AGE_S } from "../lib/paddle";

const SECRET = "pdl_ntfset_test_secret";
const now = 1_800_000_000;

async function sign(body: string, secret = SECRET, ts = now) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}:${body}`));
  const hex = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `ts=${ts};h1=${hex}`;
}

describe("the Paddle signature", () => {
  const body = JSON.stringify({ event_type: "subscription.activated" });

  it("accepts a genuine delivery", async () => {
    await expect(verifySignature(body, await sign(body), SECRET, now)).resolves.toBeUndefined();
  });

  it("refuses a body altered after signing", async () => {
    const header = await sign(body);
    await expect(verifySignature(body.replace("activated", "canceled"), header, SECRET, now))
      .rejects.toThrow(/does not match/);
  });

  it("refuses another secret", async () => {
    await expect(verifySignature(body, await sign(body, "pdl_ntfset_wrong"), SECRET, now))
      .rejects.toThrow(/does not match/);
  });

  it("refuses a stale delivery", async () => {
    const header = await sign(body, SECRET, now - MAX_AGE_S - 1);
    await expect(verifySignature(body, header, SECRET, now)).rejects.toThrow(/stale/);
  });

  it("refuses one timestamped in the future by more than the window", async () => {
    const header = await sign(body, SECRET, now + MAX_AGE_S + 1);
    await expect(verifySignature(body, header, SECRET, now)).rejects.toThrow(/stale/);
  });

  it("refuses a malformed header without throwing something else", async () => {
    for (const h of ["", "nonsense", "ts=1", "h1=abc"]) {
      await expect(verifySignature(body, h, SECRET, now)).rejects.toBeInstanceOf(WebhookError);
    }
  });

  it("refuses a non-numeric timestamp", async () => {
    await expect(verifySignature(body, "ts=soon;h1=abc", SECRET, now)).rejects.toThrow(/not a number/);
  });

  it("refuses when no secret is configured", async () => {
    await expect(verifySignature(body, await sign(body), "", now)).rejects.toThrow(/no endpoint secret/);
  });
});
