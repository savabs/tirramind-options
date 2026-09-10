# Sign-in

Identity only. This deployment establishes who someone is and issues a session;
it never decides what they have paid for. That answer comes from the service that
owns the subscriber record, where the rules about grace windows and refunds are
already written and tested. A second copy of those rules here would be a second
thing to keep correct about money, and the copy nobody watches is the one that
drifts.

## The flow

1. `/auth/start` mints a state value and a PKCE verifier, parks them in a
   ten-minute cookie, and redirects to Google.
2. `/auth/callback` checks the state, exchanges the code, reads the id_token, and
   sets a signed session cookie for twelve hours.
3. `/auth/me` says who is signed in. `/auth/logout` drops the cookie.
4. The Terminal calls the fit service's `/me` with that session. The fit service
   matches the verified email against the subscriber record and answers.

Nobody chooses a password, so there is no password for us to lose.

## Linking, for the person who paid with a different address

Signing in with an unrecognised Google account is not the same answer as "you
have not paid", so it returns `needs_link` rather than a refusal. The Terminal
then asks for the key we emailed, once, and `POST /link` binds that Google
account to the subscription permanently. A subscription can carry several
accounts, which is what someone with a work and a personal address actually
needs.

## The token

`payload.signature`, both base64url, HMAC-SHA256 over the payload bytes.
Deliberately not a JWT: there is no algorithm field, so there is no algorithm to
confuse, and no library to keep current. It is minted in TypeScript and verified
in Python, so the two implementations pin each other with a shared fixture in
`tests/test_session.py`; if the formats ever drift, that test fails rather than
nobody being able to sign in.

## Setting it up

Three secrets, none of them in this repository:

```bash
wrangler pages secret put GOOGLE_CLIENT_ID     --project-name=tirramind-options
wrangler pages secret put GOOGLE_CLIENT_SECRET --project-name=tirramind-options
wrangler pages secret put SESSION_SECRET       --project-name=tirramind-options
```

`SESSION_SECRET` must be the same value the fit service reads from
`TMO_SESSION_SECRET`: one side signs, the other verifies.

The Google OAuth client is created in the Google Cloud console, as a Web
application, with the redirect URI set to `https://<host>/auth/callback`.

Locally: copy `.dev.vars.example` to `.dev.vars`, fill it in, and run
`npm run dev`.
