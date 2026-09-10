/**
 * Begin sign-in: mint the one-time secrets, park them in a cookie, redirect.
 *
 * The state and the PKCE verifier are carried in a short-lived signed cookie
 * rather than in KV. It is one fewer binding to configure, it cannot go stale,
 * and it expires by itself. Ten minutes is longer than anyone takes to pick a
 * Google account and short enough that a stolen cookie is worth nothing.
 */

import { authorizeUrl, challengeFor, discover, randomToken } from "../../lib/google";

interface Env {
  GOOGLE_CLIENT_ID: string;
  SESSION_SECRET: string;
}

export const FLOW_COOKIE = "tmo_flow";

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  if (!env.GOOGLE_CLIENT_ID || !env.SESSION_SECRET) {
    return new Response("sign-in is not configured", { status: 503 });
  }
  const url = new URL(request.url);
  const redirectUri = `${url.origin}/auth/callback`;

  const state = randomToken();
  const verifier = randomToken();
  const challenge = await challengeFor(verifier);

  // Where to land afterwards. Only same-origin paths, so this cannot be turned
  // into an open redirect by handing someone a crafted link.
  const next = url.searchParams.get("next") || "/";
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";

  const d = await discover();
  const flow = btoa(JSON.stringify({ state, verifier, next: safeNext }));

  return new Response(null, {
    status: 302,
    headers: {
      Location: authorizeUrl(d, {
        clientId: env.GOOGLE_CLIENT_ID, redirectUri, state, challenge,
        loginHint: url.searchParams.get("email") || undefined,
      }),
      "Set-Cookie": `${FLOW_COOKIE}=${flow}; HttpOnly; Secure; SameSite=Lax; Path=/auth; Max-Age=600`,
      "Cache-Control": "no-store",
    },
  });
};
