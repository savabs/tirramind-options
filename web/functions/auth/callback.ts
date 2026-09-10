/**
 * Google sends the browser back here. Turn the code into a session, or refuse.
 *
 * Every failure is a redirect to a page that explains it, not a stack trace: the
 * person on the other end typed nothing and can fix nothing, so the only useful
 * output is a sentence they can act on or send us.
 */

import { discover, exchange, readIdToken } from "../../lib/google";
import { cookie, sign, SESSION_TTL_S } from "../../lib/token";
import { FLOW_COOKIE } from "./start";

interface Env {
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  SESSION_SECRET: string;
}

function readFlow(header: string | null): { state: string; verifier: string; next: string } | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k !== FLOW_COOKIE) continue;
    try {
      return JSON.parse(atob(v.join("=")));
    } catch {
      return null;
    }
  }
  return null;
}

const fail = (reason: string) => new Response(null, {
  status: 302,
  headers: {
    Location: `/signed-out?error=${encodeURIComponent(reason)}`,
    "Set-Cookie": `${FLOW_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/auth; Max-Age=0`,
    "Cache-Control": "no-store",
  },
});

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  const url = new URL(request.url);

  const googleError = url.searchParams.get("error");
  if (googleError) return fail(googleError === "access_denied"
    ? "You cancelled the Google sign-in." : `Google refused: ${googleError}`);

  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const flow = readFlow(request.headers.get("Cookie"));
  if (!code || !state) return fail("Google did not send a sign-in code.");
  if (!flow) return fail("The sign-in took too long, or cookies are blocked. Try again.");
  // The state check is what stops someone else's completed sign-in being
  // replayed into this browser.
  if (flow.state !== state) return fail("The sign-in did not match this browser. Try again.");

  let profile;
  try {
    const d = await discover();
    const { id_token } = await exchange(d, {
      code, verifier: flow.verifier,
      clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET,
      redirectUri: `${url.origin}/auth/callback`,
    });
    profile = readIdToken(id_token, { clientId: env.GOOGLE_CLIENT_ID, issuer: d.issuer });
  } catch (e) {
    return fail(e instanceof Error ? e.message : "Sign-in failed.");
  }

  const now = Math.floor(Date.now() / 1000);
  const token = await sign({
    sub: profile.sub, email: profile.email, email_verified: true, name: profile.name,
    iat: now, exp: now + SESSION_TTL_S,
  }, env.SESSION_SECRET);

  const headers = new Headers({ Location: flow.next || "/", "Cache-Control": "no-store" });
  headers.append("Set-Cookie", cookie(token));
  headers.append("Set-Cookie",
    `${FLOW_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/auth; Max-Age=0`);
  return new Response(null, { status: 302, headers });
};
