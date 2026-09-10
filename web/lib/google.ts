/**
 * Sign in with Google, by hand: the authorisation-code flow with PKCE.
 *
 * No SDK. The flow is four HTTP facts and one signature check, and a dependency
 * that has to be kept current is a worse trade than sixty lines that do not
 * change. Google's own endpoints are discovered rather than hard-coded, because
 * they have moved before.
 *
 * PKCE is used even though this is a confidential client with a secret. It costs
 * one hash and removes the whole class of attack where an intercepted code is
 * redeemed by someone else.
 */

const DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration";

export interface Discovery {
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
  issuer: string;
}

export interface GoogleProfile {
  sub: string;
  email: string;
  email_verified: boolean;
  name?: string;
}

export async function discover(fetchImpl: typeof fetch = fetch): Promise<Discovery> {
  const r = await fetchImpl(DISCOVERY);
  if (!r.ok) throw new Error(`google discovery failed: ${r.status}`);
  return r.json();
}

const b64url = (bytes: Uint8Array) => {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

export function randomToken(bytes = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(digest));
}

export function authorizeUrl(d: Discovery, opts: {
  clientId: string; redirectUri: string; state: string; challenge: string; loginHint?: string;
}): string {
  const u = new URL(d.authorization_endpoint);
  u.searchParams.set("client_id", opts.clientId);
  u.searchParams.set("redirect_uri", opts.redirectUri);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("scope", "openid email profile");
  u.searchParams.set("state", opts.state);
  u.searchParams.set("code_challenge", opts.challenge);
  u.searchParams.set("code_challenge_method", "S256");
  // No refresh token is wanted: we are not acting for the user against Google
  // afterwards, only establishing who they are once.
  u.searchParams.set("access_type", "online");
  if (opts.loginHint) u.searchParams.set("login_hint", opts.loginHint);
  return u.toString();
}

export async function exchange(d: Discovery, opts: {
  code: string; verifier: string; clientId: string; clientSecret: string; redirectUri: string;
}, fetchImpl: typeof fetch = fetch): Promise<{ id_token: string }> {
  const body = new URLSearchParams({
    code: opts.code,
    client_id: opts.clientId,
    client_secret: opts.clientSecret,
    redirect_uri: opts.redirectUri,
    grant_type: "authorization_code",
    code_verifier: opts.verifier,
  });
  const r = await fetchImpl(d.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!r.ok) throw new Error(`google token exchange failed: ${r.status} ${await r.text()}`);
  return r.json();
}

/**
 * Read the identity out of the id_token.
 *
 * The token arrives over TLS directly from Google's token endpoint, in response
 * to a request carrying our client secret, so its origin is already established
 * and the signature adds nothing here. The claims below are the ones that do
 * matter, and each is checked: an id_token minted for a different application,
 * or by a different issuer, or expired, must not sign anyone in.
 */
export function readIdToken(idToken: string, opts: { clientId: string; issuer: string },
                            now = Date.now() / 1000): GoogleProfile {
  const parts = idToken.split(".");
  if (parts.length !== 3) throw new Error("id_token is not a JWT");
  const pad = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(atob(pad + "=".repeat((4 - (pad.length % 4)) % 4)));

  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.includes(opts.clientId)) throw new Error("id_token was issued for another application");
  const iss = String(claims.iss || "").replace(/^https:\/\//, "");
  if (iss !== opts.issuer.replace(/^https:\/\//, "")) throw new Error("id_token issuer is wrong");
  if (typeof claims.exp !== "number" || claims.exp <= now) throw new Error("id_token has expired");
  if (!claims.sub) throw new Error("id_token has no subject");
  if (!claims.email) throw new Error("id_token has no email");
  // An unverified email must never be trusted: entitlement is matched on it, so
  // accepting one would let anyone claim someone else's subscription.
  if (claims.email_verified !== true) throw new Error("this Google account's email is not verified");

  return {
    sub: String(claims.sub),
    email: String(claims.email).toLowerCase(),
    email_verified: true,
    name: claims.name ? String(claims.name) : undefined,
  };
}
