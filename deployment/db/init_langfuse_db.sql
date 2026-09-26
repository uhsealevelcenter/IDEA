-- Idempotent setup for Langfuse's dedicated Postgres role and schema on the
-- existing `db` service's database (see docker-compose.yml). Mirrors
-- litellm/init_litellm_db.sql exactly - see that file's comments for the
-- rationale. Safe to re-run (e.g. on every deploy) - only creates/adjusts
-- what's missing and never drops existing data.
--
-- Do NOT run this file directly with `psql -f`; it expects two psql
-- variables to be supplied on the command line (see
-- langfuse/setup_langfuse_db.sh, which is the intended entry point):
--   -v langfuse_password='<value of LANGFUSE_DB_PASSWORD from .env>'
--   -v dbname='<value of POSTGRES_DB from .env>'

-- 1. Role: create it if missing, otherwise just make sure the password
--    matches .env (e.g. after a manual rotation).
--
-- NOTE: this uses psql's client-side \if/\gset instead of a server-side
-- DO $$ ... $$ block on purpose - psql does NOT interpolate :'variables'
-- inside dollar-quoted ($$) strings, so :'langfuse_password' would be sent
-- to the server literally (and fail) if this were a DO block instead.
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'langfuse') AS langfuse_role_exists \gset
\if :langfuse_role_exists
  ALTER ROLE langfuse WITH LOGIN PASSWORD :'langfuse_password';
\else
  CREATE ROLE langfuse WITH LOGIN PASSWORD :'langfuse_password';
\endif

-- 2. Schema: create it owned by langfuse if missing. If it already exists
--    (e.g. a prior deploy, or migrated in from elsewhere), leave its
--    contents alone - only fix ownership below so Langfuse's own Prisma
--    migrations can ALTER/DROP its own tables going forward.
CREATE SCHEMA IF NOT EXISTS langfuse AUTHORIZATION langfuse;
ALTER SCHEMA langfuse OWNER TO langfuse;

GRANT CONNECT ON DATABASE :"dbname" TO langfuse;
GRANT USAGE, CREATE ON SCHEMA langfuse TO langfuse;

-- 3. Re-own any objects already inside the schema (e.g. left over from a
--    previous deploy that ran migrations as the Postgres superuser
--    instead of the langfuse role) so `langfuse` owns everything it needs
--    to ALTER/DROP during future `prisma migrate`/`db push` runs.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'langfuse' LOOP
    EXECUTE format('ALTER TABLE langfuse.%I OWNER TO langfuse', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'langfuse' LOOP
    EXECUTE format('ALTER SEQUENCE langfuse.%I OWNER TO langfuse', r.sequencename);
  END LOOP;
  FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'langfuse' LOOP
    EXECUTE format('ALTER VIEW langfuse.%I OWNER TO langfuse', r.viewname);
  END LOOP;
END
$$;

-- 4. Default privileges so tables/sequences created by *future* migrations
--    (run as the langfuse role itself) don't need this script re-run.
ALTER DEFAULT PRIVILEGES IN SCHEMA langfuse GRANT ALL PRIVILEGES ON TABLES TO langfuse;
ALTER DEFAULT PRIVILEGES IN SCHEMA langfuse GRANT ALL PRIVILEGES ON SEQUENCES TO langfuse;
