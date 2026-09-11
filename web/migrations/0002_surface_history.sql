-- The surface archive.
--
-- Every fit, kept. Two things live in one row because they answer two different
-- questions from the same instant: the headline numbers, which is what a time
-- series of "how good was the fit, and what was the market doing" needs, and the
-- full payload, which is what someone reconstructing a surface needs.
--
-- Sizing, so the choice is checked rather than assumed: about 19 KB per currency
-- per fit, two currencies, every thirty minutes. That is 1.8 MB a day and under
-- 700 MB a year, against a 5 GB free allowance. Roughly seven years. If it ever
-- outgrows that, the payload moves to object storage and this table keeps the
-- index, which is the half the queries actually use.
CREATE TABLE IF NOT EXISTS surface_history (
  id            TEXT PRIMARY KEY,   -- "BTC|2026-09-11T04:13:47+00:00"
  currency      TEXT NOT NULL,
  as_of         TEXT NOT NULL,
  as_of_s       REAL NOT NULL,      -- epoch seconds, for range queries
  engine        TEXT,               -- the fit is a property of the engine too
  forward       REAL,
  rmse_vol_pts  REAL,
  inside_bid_ask REAL,
  quotes_fitted INTEGER,
  expiries      INTEGER,
  butterfly_violations INTEGER,
  calendar_violations  INTEGER,
  executable_arbs      INTEGER,
  atm_30d       REAL,               -- one headline series, interpolated at 30 days
  payload       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS surface_history_series ON surface_history (currency, as_of_s);
