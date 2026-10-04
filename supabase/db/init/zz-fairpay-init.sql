-- =====================================================================
-- FairPay Engine - first-boot database initialisation
-- ---------------------------------------------------------------------
-- Mounted into /docker-entrypoint-initdb.d by docker-compose.yml.
-- PostgreSQL runs files in that folder ONLY when the data volume is
-- empty (first start), so an existing database is never reseeded.
--
-- The file name starts with "zz-" so it runs after the Supabase image's
-- own migrate.sh, which creates the roles and schemas this relies on.
--
-- The image runs init scripts as `supabase_admin`; the application
-- connects as `postgres`, so the FairPay objects are created as
-- `postgres` to match the ownership of an existing installation.
-- The SQL files themselves are used unchanged.
-- =====================================================================

\set ON_ERROR_STOP on

SET ROLE postgres;

-- Schema, views and Bobby's 30-day dataset
\i /fairpay-init/bobby_fairpay_seed.sql

-- Earnings Opportunity Simulator history
\i /fairpay-init/simulations_table.sql

-- Telegram evidence submissions
\i /fairpay-init/telegram_tables.sql

RESET ROLE;
