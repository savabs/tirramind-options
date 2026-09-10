-- Subscriber access state, and the events that produced it.
--
-- Two tables because they answer two different questions. `subscribers` is the
-- current answer to "may this person in", read on every gated request.
-- `events` is the record of what Paddle told us and when, which is what makes a
-- wrong answer diagnosable afterwards instead of merely wrong.

CREATE TABLE IF NOT EXISTS subscribers (
  subscription_id TEXT PRIMARY KEY,
  customer_id     TEXT,
  email           TEXT,
  tier            TEXT,
  active          INTEGER NOT NULL DEFAULT 0,
  -- Seconds since the epoch. active_until grants access past `active`, which is
  -- how a cancellation honours the period already paid for; expires_at is a hard
  -- ceiling that overrides it, which is how a refund revokes at once.
  active_until    REAL,
  expires_at      REAL,
  api_key         TEXT UNIQUE,
  updated_at      REAL NOT NULL
);

-- Lookups the gate actually performs. Email is matched case-insensitively
-- against a verified Google address, so it is stored folded.
CREATE INDEX IF NOT EXISTS subscribers_email ON subscribers (email);

-- A sign-in account bound to a subscription, for the person who paid with one
-- address and signs in with another. Many-to-one on purpose: a work and a
-- personal account are the same customer.
CREATE TABLE IF NOT EXISTS identities (
  sub             TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  linked_at       REAL NOT NULL,
  FOREIGN KEY (subscription_id) REFERENCES subscribers (subscription_id)
);

-- The replay guard. Paddle reuses an event id on retries, so a repeat is a
-- no-op rather than a double write, and a captured webhook replayed inside the
-- signature's time window is refused.
CREATE TABLE IF NOT EXISTS events (
  event_id    TEXT PRIMARY KEY,
  event_type  TEXT,
  occurred_at TEXT,
  received_at REAL NOT NULL,
  applied     INTEGER NOT NULL DEFAULT 0,
  reason      TEXT,
  payload     TEXT
);

CREATE INDEX IF NOT EXISTS events_received ON events (received_at);

-- Refunds and chargebacks, kept against the subscriber rather than only in a
-- log, because "did this customer get their money back" is asked later.
CREATE TABLE IF NOT EXISTS adjustments (
  adjustment_id   TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  action          TEXT,
  status          TEXT,
  kind            TEXT,
  revoked         INTEGER NOT NULL DEFAULT 0,
  recorded_at     REAL NOT NULL
);
