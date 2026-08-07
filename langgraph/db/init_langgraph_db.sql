\set ON_ERROR_STOP on

SELECT format('CREATE ROLE idea_langgraph LOGIN PASSWORD %L', :'langgraph_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'idea_langgraph')\gexec

ALTER ROLE idea_langgraph PASSWORD :'langgraph_password';
CREATE SCHEMA IF NOT EXISTS idea_langgraph AUTHORIZATION idea_langgraph;
ALTER ROLE idea_langgraph IN DATABASE :dbname SET search_path TO idea_langgraph, public;
REVOKE ALL ON SCHEMA idea_langgraph FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA idea_langgraph TO idea_langgraph;
