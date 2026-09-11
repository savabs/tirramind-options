/**
 * The surface, from the same origin as everything else.
 *
 * The fit runs in GitHub Actions every thirty minutes and publishes its JSON
 * beside the public page. Fetching that from the browser would be a
 * cross-origin request to somebody else's host with its own cache rules; going
 * through here keeps the Terminal on one origin, lets the edge cache it, and
 * means the browser needs to know only about us.
 *
 * Deliberately not gated. The crypto surface is the free part of the product and
 * the public page already shows it; a login wall in front of something already
 * public would be theatre.
 */

interface Env { SURFACE_URL?: string }

const DEFAULT_URL = "https://savabs.github.io/tirramind-options/surface.json";
/** Half the refit interval: never stale by more than one cycle, and a burst of
 *  readers costs one origin fetch rather than one each. */
const CACHE_S = 900;

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const upstream = ctx.env.SURFACE_URL || DEFAULT_URL;
  const r = await fetch(upstream, {
    headers: { "User-Agent": "tirramind-options-terminal/0.1" },
    cf: { cacheTtl: CACHE_S, cacheEverything: true },
  });
  if (!r.ok) {
    // Say which half failed. "The surface is unavailable" with no cause is the
    // kind of message that costs an hour later.
    return new Response(JSON.stringify({
      error: "the surface could not be fetched", upstream, status: r.status,
    }), { status: 502, headers: { "Content-Type": "application/json" } });
  }
  return new Response(await r.text(), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=60, s-maxage=${CACHE_S}`,
    },
  });
};
