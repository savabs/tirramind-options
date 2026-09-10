# Sign-in and billing

This deployment establishes who someone is, receives Paddle's webhooks, holds the
subscriber record in D1, and answers what a person may use.

It did not start that way. Entitlement was deliberately kept off the edge so the
money rules would exist once, in Python. Then the machine the webhook ran on went
dark, and a refund that cannot reach us is a customer keeping access they have
been repaid for. Moving the webhook moves the record, and the rules read the
record, so the rules followed.

So two implementations of those rules now exist. `lib/rules.json` is the answer
to that: a table of cases both test suites read, so neither language can change a
rule without the other failing. If you change a rule, change the table first.

## The flow

1. `/auth/start` mints a state value and a PKCE verifier, parks them in a
   ten-minute cookie, and redirects to Google.
2. `/auth/callback` checks the state, exchanges the code, reads the id_token, and
   sets a signed session cookie for twelve hours.
3. `/auth/me` says who is signed in. `/auth/logout` drops the cookie.
4. `/auth/entitlement` matches the verified email, or an explicitly linked
   account, against the subscriber record and says what they may use.

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

Billing needs three more: `PADDLE_WEBHOOK_SECRET` from the notification
destination, `PADDLE_API_KEY` so a subscriber's email can be resolved (no webhook
payload carries one, and sign-in matches on it), and `TMO_TIER_PRICE_MAP` as a
plain var, since a price that is not listed provisions nothing.

Cloudflare Pages only picks up secret and var changes on a **new deployment**.
Setting one and expecting a live deployment to see it does not work.

The Google OAuth client is created in the Google Cloud console, as a Web
application, with the redirect URI set to `https://<host>/auth/callback`.

Locally: copy `.dev.vars.example` to `.dev.vars`, fill it in, and run
`npm run dev`.
