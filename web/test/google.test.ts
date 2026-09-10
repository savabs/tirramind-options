import { describe, expect, it } from "vitest";
import { authorizeUrl, challengeFor, exchange, readIdToken } from "../lib/google";

const D = {
  authorization_endpoint: "https://accounts.google.com/o/oauth2/v2/auth",
  token_endpoint: "https://oauth2.googleapis.com/token",
  jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
  issuer: "https://accounts.google.com",
};
const CLIENT = "123.apps.googleusercontent.com";
const now = 1_800_000_000;

const b64url = (o: unknown) =>
  btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const idToken = (claims: Record<string, unknown>) =>
  `${b64url({ typ: "JWT" })}.${b64url(claims)}.signature`;
const good = {
  iss: "https://accounts.google.com", aud: CLIENT, sub: "u-1",
  email: "Someone@Example.com", email_verified: true, name: "Someone", exp: now + 600,
};

describe("the authorize URL", () => {
  it("asks for exactly what is needed and no more", () => {
    const u = new URL(authorizeUrl(D, { clientId: CLIENT, redirectUri: "https://x/auth/callback",
      state: "st", challenge: "ch" }));
    expect(u.searchParams.get("scope")).toBe("openid email profile");
    expect(u.searchParams.get("response_type")).toBe("code");
    expect(u.searchParams.get("access_type")).toBe("online");
  });

  it("carries the PKCE challenge, not the verifier", () => {
    const u = new URL(authorizeUrl(D, { clientId: CLIENT, redirectUri: "https://x/auth/callback",
      state: "st", challenge: "ch" }));
    expect(u.searchParams.get("code_challenge")).toBe("ch");
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.toString()).not.toContain("code_verifier");
  });

  it("hashes the verifier into a different value", async () => {
    const c = await challengeFor("a-verifier");
    expect(c).not.toBe("a-verifier");
    expect(c).toBe(await challengeFor("a-verifier"));
    expect(c).not.toBe(await challengeFor("another-verifier"));
  });
});

describe("the token exchange", () => {
  it("sends the verifier and the secret, as a form", async () => {
    let seen: { url: string; init: RequestInit } | null = null;
    const fake = (async (url: string, init: RequestInit) => {
      seen = { url, init };
      return new Response(JSON.stringify({ id_token: "t" }), { status: 200 });
    }) as unknown as typeof fetch;
    await exchange(D, { code: "c", verifier: "v", clientId: CLIENT,
      clientSecret: "s", redirectUri: "https://x/auth/callback" }, fake);
    const body = new URLSearchParams(seen!.init.body as string);
    expect(seen!.url).toBe(D.token_endpoint);
    expect(body.get("code_verifier")).toBe("v");
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("client_secret")).toBe("s");
  });

  it("raises when Google refuses", async () => {
    const fake = (async () => new Response("bad code", { status: 400 })) as unknown as typeof fetch;
    await expect(exchange(D, { code: "c", verifier: "v", clientId: CLIENT,
      clientSecret: "s", redirectUri: "https://x" }, fake)).rejects.toThrow(/400/);
  });
});

describe("reading the id_token", () => {
  it("accepts a good one and lowercases the email", () => {
    const p = readIdToken(idToken(good), { clientId: CLIENT, issuer: D.issuer }, now);
    expect(p).toEqual({ sub: "u-1", email: "someone@example.com",
                        email_verified: true, name: "Someone" });
  });

  it("refuses a token minted for another application", () => {
    expect(() => readIdToken(idToken({ ...good, aud: "999.apps.googleusercontent.com" }),
      { clientId: CLIENT, issuer: D.issuer }, now)).toThrow(/another application/);
  });

  it("refuses another issuer", () => {
    expect(() => readIdToken(idToken({ ...good, iss: "https://evil.example" }),
      { clientId: CLIENT, issuer: D.issuer }, now)).toThrow(/issuer/);
  });

  it("refuses an expired token", () => {
    expect(() => readIdToken(idToken({ ...good, exp: now - 1 }),
      { clientId: CLIENT, issuer: D.issuer }, now)).toThrow(/expired/);
  });

  it("refuses an unverified email, which is what entitlement is matched on", () => {
    expect(() => readIdToken(idToken({ ...good, email_verified: false }),
      { clientId: CLIENT, issuer: D.issuer }, now)).toThrow(/not verified/);
  });

  it("accepts an aud that is a list containing us", () => {
    const p = readIdToken(idToken({ ...good, aud: ["other", CLIENT] }),
      { clientId: CLIENT, issuer: D.issuer }, now);
    expect(p.sub).toBe("u-1");
  });

  it("refuses something that is not a JWT at all", () => {
    expect(() => readIdToken("nonsense", { clientId: CLIENT, issuer: D.issuer }, now))
      .toThrow(/not a JWT/);
  });
});
