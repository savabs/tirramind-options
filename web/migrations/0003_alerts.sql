-- Alerts: who wants telling, about what, and what was actually sent.
--
-- The delivery log is not bookkeeping for its own sake. An alert that silently
-- stopped arriving is worse than no alerts at all, because the reader believes
-- the book is clean when nobody is looking. The log is how that becomes visible.

CREATE TABLE IF NOT EXISTS alert_subscriptions (
  id              TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,        -- the paying subscriber
  sub             TEXT,                 -- the sign-in account that created it
  kind            TEXT NOT NULL,        -- executable_arb | surface_integrity
  url             TEXT NOT NULL,
  secret          TEXT NOT NULL,        -- so the receiver can verify it is us
  min_edge_usd    REAL NOT NULL DEFAULT 0,
  currency        TEXT,                 -- null means every currency
  active          INTEGER NOT NULL DEFAULT 1,
  created_at      REAL NOT NULL,
  last_error      TEXT,
  last_sent_at    REAL
);

CREATE INDEX IF NOT EXISTS alert_subs_owner ON alert_subscriptions (subscription_id);
CREATE INDEX IF NOT EXISTS alert_subs_live ON alert_subscriptions (active, kind);

-- What each subscription has already been told about, so the same violation
-- persisting across refits is reported once rather than every thirty minutes.
-- An alert that repeats itself is an alert people turn off.
CREATE TABLE IF NOT EXISTS alert_seen (
  subscription  TEXT NOT NULL,
  fingerprint   TEXT NOT NULL,
  last_sent_at  REAL NOT NULL,
  PRIMARY KEY (subscription, fingerprint)
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
  id           TEXT PRIMARY KEY,
  subscription TEXT NOT NULL,
  sent_at      REAL NOT NULL,
  status       INTEGER,
  ok           INTEGER NOT NULL DEFAULT 0,
  detail       TEXT,
  items        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS alert_deliveries_when ON alert_deliveries (subscription, sent_at);
