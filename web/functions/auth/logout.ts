/** Drop the session cookie. POST only, so a stray link cannot sign anyone out. */

import { clearCookie } from "../../lib/token";

export const onRequestPost: PagesFunction = async () =>
  new Response(JSON.stringify({ signed_in: false }), {
    headers: { "Content-Type": "application/json", "Set-Cookie": clearCookie(),
               "Cache-Control": "no-store" },
  });
