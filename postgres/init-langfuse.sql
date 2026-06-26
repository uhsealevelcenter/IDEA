-- Create the Langfuse database on first postgres initialization.
-- This script only runs when the postgres data directory is empty (fresh volume).
-- For existing deployments, create manually: CREATE DATABASE langfuse;
SELECT 'CREATE DATABASE langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
