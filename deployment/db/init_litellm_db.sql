-- Idempotent setup for the LiteLLM proxy's dedicated Postgres role and
-- schema on the existing `db` service's database (see docker-compose.yml).
-- Safe to re-run (e.g. on every deploy) - only creates/adjusts what's
-- missing and never drops existing data.
--
-- Do NOT run this file directly with `psql -f`; it expects two psql
-- variables to be supplied on the command line (see
-- litellm/setup_litellm_db.sh, which is the intended entry point):
--   -v litellm_password='<value of LITELLM_DB_PASSWORD from .env>'
--   -v dbname='<value of POSTGRES_DB from .env>'

-- 1. Role: create it if missing, otherwise just make sure the password
--    matches .env (e.g. after a manual rotation).
--
-- NOTE: this uses psql's client-side \if/\gset instead of a server-side
-- DO $$ ... $$ block on purpose - psql does NOT interpolate :'variables'
-- inside dollar-quoted ($$) strings, so :'litellm_password' would be sent
-- to the server literally (and fail) if this were a DO block instead.
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'litellm') AS litellm_role_exists \gset
\if :litellm_role_exists
  ALTER ROLE litellm WITH LOGIN PASSWORD :'litellm_password';
\else
  CREATE ROLE litellm WITH LOGIN PASSWORD :'litellm_password';
\endif

-- 2. Schema: create it owned by litellm if missing. If it already exists
--    (e.g. a prior deploy, or migrated in from elsewhere), leave its
--    contents alone - only fix ownership below so LiteLLM's own Prisma
--    migrations can ALTER/DROP its own tables going forward.
CREATE SCHEMA IF NOT EXISTS litellm AUTHORIZATION litellm;
ALTER SCHEMA litellm OWNER TO litellm;

GRANT CONNECT ON DATABASE :"dbname" TO litellm;
GRANT USAGE, CREATE ON SCHEMA litellm TO litellm;

-- 3. Re-own any objects already inside the schema (e.g. left over from a
--    previous deploy that ran migrations as the Postgres superuser
--    instead of the litellm role) so `litellm` owns everything it needs
--    to ALTER/DROP during future `prisma migrate`/`db push` runs.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'litellm' LOOP
    EXECUTE format('ALTER TABLE litellm.%I OWNER TO litellm', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'litellm' LOOP
    EXECUTE format('ALTER SEQUENCE litellm.%I OWNER TO litellm', r.sequencename);
  END LOOP;
  FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'litellm' LOOP
    EXECUTE format('ALTER VIEW litellm.%I OWNER TO litellm', r.viewname);
  END LOOP;
END
$$;

-- 4. Default privileges so tables/sequences created by *future* migrations
--    (run as the litellm role itself) don't need this script re-run.
ALTER DEFAULT PRIVILEGES IN SCHEMA litellm GRANT ALL PRIVILEGES ON TABLES TO litellm;
ALTER DEFAULT PRIVILEGES IN SCHEMA litellm GRANT ALL PRIVILEGES ON SEQUENCES TO litellm;
