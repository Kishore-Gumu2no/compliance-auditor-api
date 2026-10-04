-- =====================================================================
-- Earnings Opportunity Simulator: saved simulations
-- ---------------------------------------------------------------------
-- Additive only. This script does not touch partners, orders, earnings,
-- incentives, deductions, settlements or any audit table/view.
-- Safe to run more than once.
--
-- The backend also applies this DDL itself on first use
-- (earnings_opportunity.ensure_simulations_table), so running this file
-- manually is optional.
-- =====================================================================

CREATE TABLE IF NOT EXISTS simulations (
  simulation_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id           TEXT NOT NULL REFERENCES partners(partner_id),
  scenario_type        TEXT NOT NULL CHECK (scenario_type IN ('hours','days','expenses')),
  scenario_inputs      JSONB NOT NULL,          -- selected values + baseline context
  baseline_earnings    NUMERIC(12,2) NOT NULL,  -- actual historical baseline
  projected_earnings   NUMERIC(12,2) NOT NULL,  -- deterministic estimate
  estimated_difference NUMERIC(12,2) NOT NULL,  -- projected - baseline
  assumptions          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {basis, notes[]}
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- History is always read as "this partner's simulations, newest first".
CREATE INDEX IF NOT EXISTS idx_simulations_partner_created
  ON simulations (partner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_simulations_created
  ON simulations (created_at DESC);
