-- =====================================================================
-- Telegram evidence-submission channel
-- ---------------------------------------------------------------------
-- Additive only. Does not touch partners, orders, earnings, incentives,
-- deductions, settlements or any audit table/view.
-- Safe to run more than once.
--
-- The bot also applies this DDL itself on first use
-- (telegram_store.ensure_tables), so running this file manually is optional.
-- =====================================================================

-- Which Telegram chat belongs to which delivery partner.
CREATE TABLE IF NOT EXISTS telegram_links (
  chat_id    BIGINT PRIMARY KEY,
  partner_id TEXT NOT NULL REFERENCES partners(partner_id),
  linked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per "PLEASE AUDIT" run. Stores the serialized output of the
-- existing audit pipeline; no financial values are computed here.
CREATE TABLE IF NOT EXISTS telegram_submissions (
  submission_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id          TEXT NOT NULL REFERENCES partners(partner_id),
  chat_id             BIGINT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('processing','complete','failed')),
  platforms           JSONB NOT NULL DEFAULT '[]'::jsonb,  -- announced platforms
  document_counts     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- per platform
  missing_fields      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- marked, never filled in
  results_by_platform JSONB,                               -- serialized audit per platform
  merged_result       JSONB,                               -- combined view for the website
  error               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at        TIMESTAMPTZ,
  seen_on_website     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_telegram_submissions_partner_created
  ON telegram_submissions (partner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_telegram_links_partner
  ON telegram_links (partner_id);
